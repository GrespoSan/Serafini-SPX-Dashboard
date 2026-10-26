"""Historical CBOE adapter; all gamma, walls and flips use the original Edge engine.

No live feed, scheduler, database, Serafini import or storage write is started here.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
from datetime import timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from gexlab import config
from gexlab.engine import gex
from gexlab.engine.calendar import NY, Session, TradingCalendar
from gexlab.models import parse_occ
from gexlab.sources.cboe import parse_chain, parse_cboe_timestamp

MODES = ("0DTE", "Prossime 4 scadenze", "Tutte (finestra Edge)")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 100 * 1024 * 1024


def _reject_constant(value):
    raise ValueError("Il JSON contiene valori numerici non finiti.")


def read_snapshot(raw: bytes) -> tuple[dict, bytes]:
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Fotografia vuota o superiore a 25 MiB.")
    try:
        if raw[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                body = stream.read(MAX_JSON_BYTES + 1)
        else:
            body = raw
        if len(body) > MAX_JSON_BYTES:
            raise ValueError("Fotografia decompressa superiore a 100 MiB.")
        obj = json.loads(body, parse_constant=_reject_constant)
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Fotografia JSON o gzip non valida.") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("data"), dict):
        raise ValueError("Atteso il payload originale CBOE con il campo data.")
    return obj, body


@lru_cache(maxsize=32)
def archive_calendar(day):
    """Offline NYSE session calendar, used as a US-equity session proxy for Edge.

    Preserve Edge's settlement conventions: SPX/NDX AM 09:30; weeklies PM at
    session close. This is not an authoritative CBOE settlement calendar.
    Fail explicitly rather than silently falling back to weekdays.
    """
    start, end = day - timedelta(days=20), day + timedelta(days=35)
    try:
        exchange = xcals.get_calendar("XNYS", start=start, end=end)
        sessions = {}
        for label, row in exchange.schedule.iterrows():
            d = label.date()
            opened = row["open"].tz_convert(NY).time()
            closed = row["close"].tz_convert(NY).time()
            sessions[d] = Session(d, opened, closed)
    except (ValueError, KeyError, OverflowError):
        raise ValueError("Calendario di sessione non disponibile per questa data.") from None
    if not sessions:
        raise ValueError("Calendario di sessione vuoto.")
    return TradingCalendar(sessions, "exchange_calendars:XNYS", (start, end))


def prepare_contracts(raw: bytes, mode: str):
    if mode not in MODES:
        raise ValueError("Modalità scadenze sconosciuta.")
    obj, body = read_snapshot(raw)
    data = obj["data"]
    if not isinstance(obj.get("timestamp"), str):
        raise ValueError("Manca il timestamp CBOE.")
    try:
        timestamp = parse_cboe_timestamp(obj["timestamp"])
    except ValueError:
        raise ValueError("Timestamp CBOE atteso: YYYY-MM-DD HH:MM:SS (UTC).") from None
    spot = data.get("current_price")
    if isinstance(spot, bool) or not isinstance(spot, (int, float)) or not math.isfinite(spot) or spot <= 0:
        raise ValueError("Prezzo spot CBOE non valido.")
    options = data.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("La fotografia non contiene opzioni CBOE.")
    families, symbols = set(), set()
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Riga opzione CBOE non valida.")
        try:
            occ = parse_occ(option["option"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue  # Same unsupported-OCC policy as upstream.
        for symbol, roots in config.CBOE_ROOTS.items():
            if occ.root in roots:
                families.add(symbol)
        if option["option"] in symbols:
            raise ValueError("Contratti OCC duplicati: esposizione ambigua.")
        symbols.add(option["option"])
        oi = option.get("open_interest")
        if oi is not None and (isinstance(oi, bool) or not isinstance(oi, (int, float))
                               or not math.isfinite(oi) or oi < 0 or oi != int(oi)):
            raise ValueError("Open interest CBOE negativo, frazionario o non valido.")
        for key in ("iv", "bid", "ask"):
            value = option.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("IV o quotazioni CBOE non valide.")
    if len(families) != 1:
        raise ValueError("Attesa una sola catena indice SPX o NDX riconoscibile dai contratti OCC.")
    symbol = families.pop()
    declared = obj.get("symbol") or data.get("symbol")
    if declared and str(declared).strip().lstrip("^_").upper() != symbol:
        raise ValueError("Il simbolo dichiarato non corrisponde ai contratti OCC.")
    cal = archive_calendar(timestamp.astimezone(NY).date())
    chain = parse_chain(symbol, body, timestamp, cal)
    # Remove settled AM/PM contracts BEFORE choosing the next four expiries.
    contracts = gex.finalize(chain.contracts, chain.spot.price, chain.valuation, cal)
    dates = sorted({contract.expiry for contract in contracts})
    if mode == "0DTE":
        # The overnight Globex session starts at 18:00 New York on the
        # preceding calendar day. Its 0DTE expiry is the NEXT equity session.
        # Example: 2026-10-23 03:40 UTC = Oct 22, 23:40 New York:
        # use the 2026-10-23 expiry, not 2026-10-22.
        ny_clock = timestamp.astimezone(NY)
        trading_day = ny_clock.date()
        if ny_clock.hour >= 18:
            sessions_ahead = sorted(d for d in cal.sessions if d > trading_day)
            if not sessions_ahead:
                raise ValueError("Sessione americana successiva non disponibile.")
            trading_day = sessions_ahead[0]
        keep = {trading_day}
    elif mode == "Prossime 4 scadenze":
        keep = set(dates[:4])
    else:
        keep = set(dates)
    contracts = [contract for contract in contracts if contract.expiry in keep]
    if not any(contract.valid for contract in contracts):
        raise ValueError("Nessun contratto non scaduto con OI e IV validi nella selezione.")
    return chain, contracts, cal


def analyze(raw: bytes, mode: str = "0DTE") -> dict:
    chain, contracts, cal = prepare_contracts(raw, mode)
    spot = chain.spot.price
    levels, grid, values = gex.compute_levels(chain.ticker, contracts, spot)
    frame = pd.DataFrame(gex.strike_rows(contracts, spot * .9, spot * 1.1))
    cov = gex.gex_cov(contracts, spot)
    return {
        "symbol": chain.ticker, "timestamp": chain.source_ts.isoformat(),
        "valuation": chain.valuation.isoformat(), "spot": spot, "mode": mode,
        "expiries": [str(d) for d in sorted({c.expiry for c in contracts})],
        "call_wall": levels.call_wall, "put_wall": levels.put_wall,
        "flip": levels.nearest_zero_gamma, "flips": levels.zero_gamma,
        "regime": levels.regime, "net": levels.net_gex, "gross": levels.gross_gex,
        "quality_pct": cov.pct, "quality_unbounded": cov.ratio is None,
        "quality_sufficient": gex.gex_gate_ok(cov),
        "valid": sum(c.valid for c in contracts), "total": len(contracts),
        "rows": frame, "curve": pd.DataFrame({"spot": grid, "net_gex": values}),
        "calendar_fallback": False, "calendar_source": cal.source,
        "oi_date": chain.oi_date, "oi_date_inferred": True,
        "sha256": hashlib.sha256(raw).hexdigest(), "model_version": config.MODEL_VERSION,
        "source": "CBOE archivio / metodologia Edge originale",
    }

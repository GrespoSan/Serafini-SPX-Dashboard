"""Massive Futures REST: one source/contract/session for Daily range and direction.

Use 1session bars and the exchange's session_end_date. Never derive the
trading day from window_start, which is the PREVIOUS calendar day.
Use trade 'close', NOT 'settlement_price', for both measures. Futures
continuous ticker 1! can roll to a different contract: name the real ticker.
No implicit Yahoo fallback and no remote data without MASSIVE_API_KEY.
"""
import math
import re
from datetime import date, timedelta

import requests

API = "https://api.massive.com/futures/v1/aggs/"
PATTERN = re.compile(r"^(ES|NQ|GC|CL)[FGHJKMNQUVXZ][0-9]{1,2}$")
PRODUCT = {"ES", "NQ", "GC", "CL"}


class MassiveSourceError(ValueError):
    pass


def _value(value):
    try:
        numeric = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise MassiveSourceError("OHLC non numerico.") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise MassiveSourceError("OHLC non valido.")
    return numeric


def parse_session(raw, ticker):
    if not isinstance(raw, dict) or raw.get("ticker") not in (None, ticker):
        raise MassiveSourceError("Il contratto restituito non corrisponde alla richiesta.")
    try:
        trading_date = date.fromisoformat(raw["session_end_date"])
        high = _value(raw["high"])
        low = _value(raw["low"])
        close = _value(raw["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MassiveSourceError("Barra sessione Massive incompleta.") from exc
    if high < low or close > high or close < low:
        raise MassiveSourceError("OHLC fuori range: impossibile validare.")
    return {"day": trading_date, "hi": high, "lo": low, "close": close,
            "ticker": ticker, "settlement_price": raw.get("settlement_price")}


def previous_sessions(ticker, target_day, api_key, *, get=None):
    """Return chronological 1session bars before target_day, on SAME contract.

    Two most recent eligible trading dates are needed for change%. They
    may straddle weekends, but never span a contract rollover unnoticed.
    No network call happens if key or ticker is absent.
    """
    if not isinstance(target_day, date):
        raise MassiveSourceError("Data di riferimento non valida.")
    ticker = str(ticker).strip().upper()
    if not PATTERN.fullmatch(ticker):
        raise MassiveSourceError("Specificare un contratto reale (es. CLZ6), non CL1!.")
    if not str(api_key or "").strip():
        raise MassiveSourceError("MASSIVE_API_KEY non configurata in Streamlit Secrets.")
    begin = target_day - timedelta(days=19)
    end = target_day - timedelta(days=1)
    requester = get or requests.get
    try:
        response = requester(
            API + ticker,
            params={"resolution": "1session",
                    "window_start.gte": begin.isoformat(),
                    "window_start.lte": end.isoformat(),
                    "sort": "window_start.asc", "limit": 50},
            headers={"Authorization": "Bearer " + api_key,
                     "Accept": "application/json"},
            timeout=(6, 18),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise MassiveSourceError("Massive non disponibile o API key/piano Futures senza permessi.") from exc
    except (TypeError, ValueError) as exc:
        raise MassiveSourceError("Risposta Massive non JSON.") from exc
    if not isinstance(payload, dict) or payload.get("status") not in ("OK", "DELAYED"):
        raise MassiveSourceError("Massive: stato non valido o accesso Futures non autorizzato.")
    if not isinstance(payload.get("results"), list):
        raise MassiveSourceError("Massive: elenco sessioni assente.")
    days = {}
    for item in payload["results"]:
        try:
            row = parse_session(item, ticker)
        except MassiveSourceError:
            continue
        if row["day"] >= target_day or row["day"].weekday() > 4:
            continue
        days[row["day"]] = row
    result = sorted(days.values(), key=lambda r: r["day"])
    if len(result) < 2:
        raise MassiveSourceError("Servono due sessioni chiuse dello stesso contratto.")
    if (target_day - result[-1]["day"]).days > 7:
        raise MassiveSourceError("La barra precedente è troppo distante dalla data di riferimento.")
    if (result[-1]["day"] - result[-2]["day"]).days > 7:
        raise MassiveSourceError("Le due sessioni non sono consecutive nell'operatività.")
    return result


def previous_daily_context(bars):
    """Pure: both numbers from the same MASSIVE 1session CLOSE prices."""
    if len(bars) < 2:
        raise MassiveSourceError("Mancano le due chiusure Daily.")
    before, yesterday = bars[-2:]
    if before["ticker"] != yesterday["ticker"]:
        raise MassiveSourceError("Non confrontare contratti futures differenti.")
    if yesterday["day"] <= before["day"]:
        raise MassiveSourceError("Date delle sessioni non crescenti.")
    amount = yesterday["hi"] - yesterday["lo"]
    pct_range = 100.0 * amount / yesterday["close"]
    previous_amount = before["hi"] - before["lo"]
    previous_pct_range = 100.0 * previous_amount / before["close"]
    # Percent-normalized daily ranges allow meaningful comparison across
    # adjacent sessions even when price levels have moved.
    if math.isclose(pct_range, previous_pct_range, abs_tol=1e-9, rel_tol=0):
        range_trend = "INVARIATO"
    else:
        range_trend = "ESPANSIONE" if pct_range > previous_pct_range else "COMPRESSIONE"
    change = 100.0 * (yesterday["close"] / before["close"] - 1.0)
    return {
        "status": "VALIDO", "day": yesterday["day"].isoformat(),
        "ticker": yesterday["ticker"],
        "source": "Massive Futures REST · 1session · trade close",
        "high": yesterday["hi"], "low": yesterday["lo"],
        "close": yesterday["close"], "previous_close": before["close"],
        "previous_close_day": before["day"].isoformat(),
        "range_points": round(amount, 6), "range_pct": round(pct_range, 6),
        "previous_range_points": round(previous_amount, 6),
        "previous_range_pct": round(previous_pct_range, 6),
        "range_trend": range_trend,
        "change_pct": round(change, 6),
        "formula": "(High-Low) / Close * 100",
        "change_formula": "(Close / previous Close - 1) * 100",
        "note": ("Stesso contratto e stesse sessioni Massive; close dell'ultimo "
                 "trade, NON settlement_price. Verificare la scadenza rispetto a 1!."),
    }

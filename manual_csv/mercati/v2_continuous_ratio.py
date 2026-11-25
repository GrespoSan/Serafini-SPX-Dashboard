"""Continuous-only, synchronized ETF/future 5m chart price mapping.

Cboe Gamma stays in original ETF units. Only STRIKE PRICES are multiplied
by the last common CLOSED five-minute ETF/Yahoo continuous-future quote
available before the original Cboe acquisition. This is a chart proxy for
TradingView's GC1!/CL1!, NOT a guarantee of exact TradingView rollover.
Never use Massive, contract-month selections or an invented ratio.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from manual_csv.mercati.future_ratio import normalize_bars

NY = ZoneInfo("America/New_York")
CONTINUOUS = {
    "GLD": {"etf": "GLD", "future": "GC=F", "tv": "COMEX:GC1!",
            "micro": "COMEX_MINI:MGC1!", "short": "GC"},
    "USO": {"etf": "USO", "future": "CL=F", "tv": "NYMEX:CL1!",
            "micro": "NYMEX:MCL1!", "short": "CL"},
}


def last_closed_continuous_pair(symbol, etf_bars, future_bars, captured_at,
                                *, now=None):
    """Deterministic no-look-ahead pairing; only exactly equal UTC bar starts."""
    if symbol not in CONTINUOUS:
        raise ValueError("Mercato continuo non riconosciuto.")
    if isinstance(captured_at, str):
        try:
            captured_at = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValueError("Ora fotografia non riconosciuta.") from exc
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("Ora originale di acquisizione con fuso non disponibile.")
    cutoff = captured_at.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Orologio privo di fuso orario.")
    if cutoff > now.astimezone(timezone.utc) + timedelta(minutes=5):
        raise ValueError("La fotografia non può essere nel futuro.")
    if now.astimezone(timezone.utc) - cutoff > timedelta(days=58):
        raise ValueError("Fotografia oltre la finestra storica M5 Yahoo.")
    etf = normalize_bars(etf_bars)
    future = normalize_bars(future_bars)
    shared = etf.index.intersection(future.index)
    options = []
    for ts in shared:
        dt = ts.to_pydatetime().astimezone(timezone.utc)
        et = dt.astimezone(NY)
        if not time(9, 30) <= et.time() < time(16, 0):
            continue
        # Time in source data is bar OPEN. Both bars must have CLOSED
        # by the real original Cboe capture. Cannot use later market values.
        if dt + timedelta(minutes=5) > cutoff:
            continue
        if cutoff - (dt + timedelta(minutes=5)) > timedelta(days=7):
            continue
        e, f = float(etf[ts]), float(future[ts])
        if isfinite(e) and e > 0 and isfinite(f) and f > 0:
            options.append((dt, e, f))
    if not options:
        raise ValueError("Nessuna coppia M5 chiusa con timestamp identico "
                         "prima della fotografia.")
    t, ep, fp = max(options, key=lambda x: x[0])
    settings = CONTINUOUS[symbol]
    ratio = fp / ep
    if not isfinite(ratio) or ratio <= 0:
        raise ValueError("Rapporto continuo non valido.")
    return {
        "etf_symbol": symbol, "etf_price": ep, "future_price": fp,
        "ratio": ratio, "bar_ny": t.astimezone(NY).isoformat(timespec="seconds"),
        "future_yahoo_ticker": settings["future"],
        "future_tv_symbol": settings["tv"],
        "source": f"Yahoo Finance {symbol}/{settings['future']} · M5 chiusa",
        "mode": "continuous-yahoo-5m",
        "captured_utc": cutoff.isoformat(timespec="seconds"),
        "continuous_proxy": True,
    }


def fetch_continuous_pair(symbol, captured_at, *, yf_module=None, now=None):
    """No Massive calls. Yahoo GC=F/CL=F are NOT strictly TV GC1!/CL1!."""
    if symbol not in CONTINUOUS:
        raise ValueError("Mercato continuo non riconosciuto.")
    if isinstance(captured_at, str):
        captured_at = datetime.fromisoformat(captured_at)
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("Data acquisizione fotografica non verificabile.")
    if yf_module is None:
        import yfinance as yf
        yf_module = yf
    settings = CONTINUOUS[symbol]
    local_day = captured_at.astimezone(NY).date()
    start = (local_day - timedelta(days=10)).isoformat()
    end = (local_day + timedelta(days=1)).isoformat()
    try:
        e = yf_module.Ticker(settings["etf"]).history(
            start=start, end=end, interval="5m",
            prepost=False, auto_adjust=False)
        f = yf_module.Ticker(settings["future"]).history(
            start=start, end=end, interval="5m",
            prepost=False, auto_adjust=False)
    except Exception as exc:
        raise ValueError("Barre M5 Yahoo non disponibili.") from exc
    return last_closed_continuous_pair(
        symbol, e, f, captured_at, now=now)


def active_snapshot_cutoff(symbol, state):
    """Only independently timed downloads: archive or deliberate direct fetch."""
    if symbol not in CONTINUOUS:
        raise ValueError("Mercato continuo non riconosciuto.")
    source = str(state.get(f"{symbol.lower()}_cboe_source", ""))
    if "Archivio privato" in source:
        return state.get(f"v2_archive_capture_rome_{symbol}")
    if source == "CSV Cboe" or "Acquisizione diretta su richiesta" in source:
        return state.get(f"{symbol.lower()}_cboe_import_rome")
    # A local import time is NOT the original Cboe acquisition time.
    return None


def load_chart_continuous_mapping(symbol, state, loader=None):
    """Isolated chart lookup with bounded success cache, no Massive ever.

    Errors remain metadata, never warning widgets. Fail closed rather
    than displaying ETF strikes under false GC/CL coordinates.
    """
    cutoff = active_snapshot_cutoff(symbol, state)
    if cutoff is None:
        return None, "Ora di acquisizione del file non documentata"
    if loader is None:
        loader = _cached_continuous
    try:
        return loader(symbol, str(cutoff)), None
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return None, str(exc)[:200]


# Cache values by BOTH market and exact Cboe acquisition timestamp.
# Never prefix parameters with "_" here: Streamlit then excludes them
# from cache hashing, potentially reusing a GC ratio for USO/CL.
import streamlit as st


@st.cache_data(ttl=900, show_spinner=False)
def _cached_continuous(symbol, cutoff):
    return fetch_continuous_pair(symbol, cutoff)

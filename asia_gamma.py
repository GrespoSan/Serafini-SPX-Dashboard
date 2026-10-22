"""Sessione asiatica ES 00:00–09:00 Roma; indipendente dai calcoli NET GAMMA."""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import math
import pandas as pd
import streamlit as st

ROME = ZoneInfo("Europe/Rome")


def _column(frame, field):
    """Seleziona solo la colonna ES=F anche con MultiIndex Yahoo."""
    if isinstance(frame.columns, pd.MultiIndex):
        for key in ((field, "ES=F"), ("ES=F", field)):
            if key in frame.columns:
                return frame[key]
        raise ValueError(f"Colonna {field} ES=F non riconosciuta.")
    if field not in frame.columns:
        raise ValueError(f"Colonna {field} mancante.")
    return frame[field]


@st.cache_data(ttl=900, max_entries=90, show_spinner=False)
def asian_session(target: date):
    """Barre 5m [00:00,09:00) Roma; verifica integrita' e OHLC."""
    import yfinance as yf

    now = datetime.now(ROME)
    if target > now.date() or (target == now.date() and now.time() < time(9, 0)):
        raise ValueError("La sessione asiatica non è ancora terminata alle 09:00.")
    if target < now.date() - timedelta(days=55):
        raise ValueError("Storico ES a 5 minuti non disponibile per questa data.")
    frame = yf.download(
        "ES=F", start=(target - timedelta(days=1)).isoformat(),
        end=(target + timedelta(days=1)).isoformat(),
        interval="5m", auto_adjust=False, progress=False,
        threads=False, prepost=True,
    )
    if frame is None or frame.empty or frame.index.tz is None:
        raise ValueError("Yahoo non ha restituito barre intraday ES valide.")
    frame = frame.copy()
    frame.index = frame.index.tz_convert(ROME)
    start = datetime.combine(target, time(0, 0), ROME)
    end = datetime.combine(target, time(9, 0), ROME)
    expected = pd.date_range(start=start, end=end, freq="5min", inclusive="left")
    bars = frame[(frame.index >= start) & (frame.index < end)].copy()
    if bars.index.has_duplicates:
        raise ValueError("Timestamp ES duplicati nella sessione asiatica.")
    missing = expected.difference(bars.index)
    extra = bars.index.difference(expected)
    if len(missing) or len(extra) or len(bars) != len(expected):
        raise ValueError(
            f"Sessione incompleta: {len(bars)}/{len(expected)} barre; "
            f"{len(missing)} mancanti, {len(extra)} fuori griglia. "
            "Non stimiamo massimo/minimo su una sessione incompleta."
        )
    high = pd.to_numeric(_column(bars, "High"), errors="coerce")
    low = pd.to_numeric(_column(bars, "Low"), errors="coerce")
    close = pd.to_numeric(_column(bars, "Close"), errors="coerce")
    if not (high.notna().all() and low.notna().all() and close.notna().all()):
        raise ValueError("Barre OHLC con valori mancanti.")
    if not all(math.isfinite(float(v)) and float(v) > 0
               for col in (high, low, close) for v in col):
        raise ValueError("Barre OHLC con valori non validi.")
    if (high < low).any() or (close > high).any() or (close < low).any():
        raise ValueError("Barre OHLC incoerenti.")
    hi, lo, last = float(high.max()), float(low.min()), float(close.iloc[-1])
    return {
        "high": hi, "low": lo, "close": last,
        "bars": len(bars), "expected": len(expected),
        "source": "Yahoo Finance ES=F · barre 5 minuti, 00:00–09:00 Roma",
    }


def close_at_nine(target: date):
    """Compatibilita' con la versione precedente."""
    result = asian_session(target)
    return {"price": result["close"], "source": result["source"],
            "time": target.isoformat() + " 09:00"}


def render(target: date, c1_es, p1_es):
    st.markdown("**Sessione asiatica — 00:00–09:00 (Roma)**")
    try:
        result = asian_session(target)
    except Exception as exc:
        st.info(f"Dati sessione asiatica non disponibili: {exc}")
        return

    hi, lo, price = result["high"], result["low"], result["close"]
    a, b, c = st.columns(3)
    a.metric("Massimo Asia", f"{hi:,.2f}")
    b.metric("Minimo Asia", f"{lo:,.2f}")
    c.metric("Chiusura ore 09:00", f"{price:,.2f}")
    st.caption(f"{result['source']} · {result['bars']}/{result['expected']} barre verificate.")

    if c1_es is None or p1_es is None or not p1_es < c1_es:
        st.info("Per classificare la posizione, seleziona C1 e P1 validi "
                "e indica il differenziale ES − SPX.")
        return

    st.caption(f"Riferimenti NET GAMMA in ES: C1 {c1_es:,.2f} · P1 {p1_es:,.2f}")
    if price > c1_es:
        zone = "Sopra C1 — zona superiore"
    elif price < p1_es:
        zone = "Sotto P1 — zona inferiore"
    else:
        zone = "Tra P1 e C1 — area flip operativa"

    if hi < p1_es:
        behavior = "Intera sessione sotto P1"
    elif lo > c1_es:
        behavior = "Intera sessione sopra C1"
    elif lo > p1_es and hi < c1_es:
        behavior = "Intera sessione fra P1 e C1"
    elif lo < p1_es and hi > c1_es:
        behavior = "La sessione ha attraversato sia P1 sia C1"
    elif lo < p1_es:
        behavior = "La sessione ha attraversato P1"
    elif hi > c1_es:
        behavior = "La sessione ha attraversato C1"
    else:
        behavior = "La sessione ha toccato uno o entrambi i livelli"

    st.markdown(f"**Posizione alle 09:00:** {zone}")
    st.markdown(f"**Andamento della sessione:** {behavior}")
    st.caption("Confronto con candidati C1/P1 sperimentali: "
               "non è una misura diretta del gamma dei dealer.")

"""Range Daily ES della seduta precedente: fonte Yahoo ES=F, senza look-ahead.

Questo intervallo Daily e' del future (non dell'indice SPX). La sessione
Yahoo puo' differire dalla candela ES1! TradingView/CME; mostrare sempre
la data e la fonte e non forzare confronti non validati.
"""
import math
from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf


@st.cache_data(ttl=21600, max_entries=80, show_spinner=False)
def previous_es_daily_range(target_day: date):
    """High-Low della barra ES=F Daily precedente, come data di seduta Yahoo.

    La query termina esclusivamente al target_day: non usa dati della data
    analizzata, nemmeno se la barra Daily di oggi e' gia' disponibile.
    """
    if not isinstance(target_day, date):
        raise ValueError("Data di riferimento Daily non valida")
    start = target_day - timedelta(days=20)
    raw = yf.download(
        "ES=F", start=start.isoformat(), end=target_day.isoformat(),
        interval="1d", auto_adjust=False, progress=False, threads=False,
    )
    if raw is None or raw.empty:
        raise ValueError("Yahoo ES=F Daily non disponibile")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if any(field not in raw.columns for field in ("High", "Low", "Close")):
        raise ValueError("Yahoo ES=F: campi Daily High/Low/Close mancanti")

    candidates = []
    for index, row in raw.iterrows():
        # Per interval=1d Yahoo etichetta le barre con la data della seduta,
        # senza utilizzare la conversione del timestamp intraday a Roma.
        session = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
        if session >= target_day or session.weekday() >= 5:
            continue
        try:
            hi, lo, close = (float(row[key]) for key in ("High", "Low", "Close"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(x) for x in (hi, lo, close)):
            continue
        if not (hi >= lo > 0 and close > 0):
            continue
        candidates.append((session, hi, lo, close))

    if not candidates:
        raise ValueError("Nessuna barra Daily completa precedente alla giornata")
    session, hi, lo, close = max(candidates, key=lambda item: item[0])
    if (target_day - session).days > 7:
        raise ValueError("Ultima barra ES Daily troppo distante dalla data analizzata")

    point_range = hi - lo
    pct = point_range / close * 100.0
    # Movimentazione della seduta precedente: Close di ieri rispetto
    # alla chiusura del giorno di negoziazione ANCORA precedente.
    # Non coincide con il Range Daily High−Low, che e' sempre >= 0.
    older = [bar for bar in candidates
             if bar[0] < session and (session - bar[0]).days <= 7]
    previous_bar = max(older, key=lambda bar: bar[0]) if older else None
    older_close = previous_bar[3] if previous_bar else None
    change_pct = ((close / older_close - 1.0) * 100.0
                  if older_close and older_close > 0 else None)
    return {
        "status": "VALIDO", "day": session.isoformat(),
        "source": "Yahoo Finance ES=F (Daily, future continuo)",
        "high": round(hi, 4), "low": round(lo, 4),
        "close": round(close, 4),
        "range_points": round(point_range, 4),
        "range_pct": round(pct, 6),
        "change_pct": round(change_pct, 6) if change_pct is not None else None,
        "previous_close": round(older_close, 4) if older_close else None,
        "previous_close_day": (previous_bar[0].isoformat()
                               if previous_bar else None),
        "change_formula": "(Close ultima seduta / Close seduta precedente - 1) * 100",
        "formula": "(High - Low) / Close * 100",
        "note": ("Barra Daily del future ES=F: puo' differire da ES1! "
                 "TradingView e dai dati CME ufficiali."),
    }

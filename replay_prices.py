"""Candele ES=F opzionali per Replay: solo barre 5m concluse entro lo snapshot.

Le quotazioni Yahoo non sono necessariamente identiche al continuo TradingView/CME.
Nessuna alterazione degli snapshot Cboe né dei calcoli NET GAMMA.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc


@st.cache_data(ttl=900, show_spinner=False, max_entries=12)
def load_es_candles(day):
    """Restituisce OHLC 5m con timestamp di chiusura timezone-aware Europe/Rome."""
    local_start = datetime.combine(day, time.min, tzinfo=ROME)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=ROME)
    # Yahoo limita i dati intraday a circa 60 giorni. Non usare barre odierne future.
    end = min(local_end.astimezone(UTC), datetime.now(UTC))
    if end <= local_start.astimezone(UTC):
        raise ValueError("La giornata scelta non è ancora iniziata")
    if (datetime.now(UTC) - local_start.astimezone(UTC)).days > 59:
        raise ValueError("Yahoo 5m non conserva normalmente questa giornata (>59 giorni)")
    raw = yf.download(
        "ES=F", start=local_start.date().isoformat(),
        end=(day + timedelta(days=2)).isoformat(),
        interval="5m", auto_adjust=False, progress=False,
        prepost=True, threads=False,
    )
    if raw is None or raw.empty:
        raise ValueError("Candele ES=F a 5 minuti non disponibili da Yahoo")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    need = ("Open", "High", "Low", "Close")
    if any(name not in raw for name in need):
        raise ValueError("OHLC mancanti nella risposta Yahoo")
    if raw.index.tz is None:
        raise ValueError("Timestamp Yahoo senza fuso orario: candele non utilizzabili")
    local_index = raw.index.tz_convert(ROME)
    out = pd.DataFrame({name.lower():pd.to_numeric(raw[name], errors="coerce")
                        for name in need}, index=raw.index)
    # Gli indici Yahoo sono aperture delle barre; si visualizzano solo a chiusura.
    out["bar_close"] = local_index + pd.Timedelta(minutes=5)
    out = out.loc[(out["bar_close"] >= local_start) &
                  (out["bar_close"] < local_end) &
                  (out["bar_close"] <= end.astimezone(ROME))]
    out = out.dropna(subset=list(name.lower() for name in need))
    if out.empty:
        raise ValueError("Nessuna candela ES=F valida nella giornata")
    return out.reset_index(drop=True)


def candles_up_to(data, downloaded_at):
    """Nessuna candela della barra ancora aperta; inclusa soltanto la chiusura <= download."""
    if downloaded_at is None:
        return data.iloc[0:0]
    return data.loc[data["bar_close"] <= downloaded_at]

"""Deviazione Serafini cash M15, come G_Deviazioni_Serafini_Lab_Metodo90_v2.pine.

Metodo adottato (ipotesi del Pine originale):
- solo chiusure delle M15 con apertura cash 08:30..14:45 Chicago;
- sigma popolazione (ddof=0) delle ultime 90/350 chiusure cash;
- riferimento = chiusura della M15 15:00..15:15 Chicago precedente;
- L90=ref+sigma90; L350=ref+sigma350; S350=ref-sigma350.
I dati sono ES=F Yahoo, NON sono garantiti identici a ES1! TradingView.
Niente dati della seduta analizzata, interpolazioni o valori presunti.
"""
import math
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

CHICAGO = ZoneInfo("America/Chicago")


def compute_cash_levels(candles, day: date):
    """Deterministico su M15 OHLC; gli indici rappresentano APERTURE M15."""
    if candles is None or candles.empty or candles.index.tz is None:
        raise ValueError("Candele M15 assenti o prive di fuso orario")
    if "Close" not in candles:
        raise ValueError("Close M15 assente")
    closes = pd.to_numeric(candles["Close"], errors="coerce")
    if isinstance(closes, pd.DataFrame):
        if closes.shape[1] != 1:
            raise ValueError("Una sola serie ES M15 richiesta")
        closes = closes.iloc[:, 0]
    frame = pd.DataFrame({"close": closes.to_numpy()},
                         index=candles.index.tz_convert(CHICAGO))
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.loc[frame["close"].apply(lambda n: math.isfinite(n) and n > 0)]
    # Il Pine utilizza time(..., '0830-1500:23456', 'America/Chicago'):
    # sono le APERTURE delle 26 barre cash, non le chiusure.
    prev = frame.loc[
        (frame.index.date < day) &
        (frame.index.dayofweek < 5) &
        (frame.index.hour == 15) & (frame.index.minute == 0)
    ]
    if prev.empty:
        raise ValueError("M15 15:00–15:15 Chicago precedente non disponibile")
    ref_time = prev.index[-1]
    session = ref_time.date()
    if (day - session).days > 5:
        raise ValueError("Ultima sessione precedente troppo distante")

    cash_mask = (
        (frame.index.date <= session) &
        (frame.index.dayofweek < 5) &
        (((frame.index.hour * 60 + frame.index.minute) >= 8 * 60 + 30) &
         ((frame.index.hour * 60 + frame.index.minute) < 15 * 60))
    )
    cash = frame.loc[cash_mask, "close"]
    today_cash = cash.loc[cash.index.date == session]
    # Un mezzo campione incompleto altererebbe sigma: non stimiamo il dato.
    expected = pd.date_range(
        start=pd.Timestamp(session).tz_localize(CHICAGO) +
              pd.Timedelta(hours=8, minutes=30),
        periods=26, freq="15min",
    )
    if len(today_cash) != 26 or not today_cash.index.equals(expected):
        raise ValueError("Ultima sessione cash M15 incompleta (richieste 26 barre)")
    selected = cash.iloc[-350:]
    if len(selected) != 350:
        raise ValueError(f"Storico cash insufficiente ({len(selected)}/350 M15)")
    # Controllo buchi sulle sedute interne dell'intervallo (la prima puo'
    # essere parziale, perche' il campione 350 e' una finestra mobile).
    first_session = selected.index[0].date()
    for d in sorted(set(selected.index.date)):
        if d == first_session:
            continue
        observed = selected.loc[selected.index.date == d].index
        day_expected = pd.date_range(
            start=pd.Timestamp(d).tz_localize(CHICAGO) +
                  pd.Timedelta(hours=8, minutes=30),
            periods=26, freq="15min",
        )
        if not observed.equals(day_expected):
            raise ValueError("Buchi nelle M15 cash Yahoo: deviazione non verificabile")

    close_ref = float(frame.loc[ref_time, "close"])
    sigma90 = float(selected.iloc[-90:].std(ddof=0))
    sigma350 = float(selected.std(ddof=0))
    if not all(math.isfinite(v) and v >= 0 for v in (sigma90, sigma350)):
        raise ValueError("Deviazione standard non valida")
    return {
        "status": "VALIDO",
        "session_chicago": session.isoformat(),
        "reference_time_chicago": ref_time.isoformat(),
        "reference_close_es": close_ref,
        "sigma90": sigma90, "sigma350": sigma350,
        "L90": close_ref + sigma90,
        "L350": close_ref + sigma350,
        "S350": close_ref - sigma350,
        "sample90": 90, "sample350": 350,
        "source": "Yahoo Finance ES=F M15",
        "method": "Cash 08:30-15:00 America/Chicago; close rif. 15:15 Chicago; sigma popolazione",
        "note": "Risultato da confrontare con Pine Serafini su ES1!: i feed possono differire.",
    }


@st.cache_data(ttl=21600, max_entries=90, show_spinner=False)
def serafini_cash_levels(day: date):
    """Un'unica richiesta M15 su richiesta della pagina; cache 6 ore."""
    start = (day - timedelta(days=42)).isoformat()
    stop = (day + timedelta(days=1)).isoformat()
    raw = yf.download("ES=F", start=start, end=stop, interval="15m",
                      auto_adjust=False, progress=False, prepost=True,
                      threads=False)
    if raw is None or raw.empty:
        raise ValueError("Yahoo ES=F M15 non disponibile")
    if isinstance(raw.columns, pd.MultiIndex):
        # Yahoo yfinance usa talvolta (campo, ticker) o (ticker, campo).
        for level in range(raw.columns.nlevels):
            if "Close" in raw.columns.get_level_values(level):
                raw.columns = raw.columns.get_level_values(level)
                break
    return compute_cash_levels(raw, day)


def add_sigma_markers(fig, levels, y_values):
    """Tre ACCENNI orizzontali sul lato estremo SINISTRO del solo 0DTE."""
    if not levels or levels.get("status") != "VALIDO":
        return 0, []
    visible_min, visible_max = min(y_values), max(y_values)
    definitions = (
        ("L90", "#58D1E1", "L90"),
        ("L350", "#72CD88", "L350"),
        ("S350", "#F08888", "S350"),
    )
    shown, clipped = 0, []
    for name, color, key in definitions:
        value = float(levels[key])
        if not math.isfinite(value):
            continue
        if not visible_min <= value <= visible_max:
            clipped.append(name)
            continue
        # X relativo al pannello, non al valore NET GAMMA:
        # un segno di circa 5% della larghezza, NON una linea estesa.
        fig.add_shape(
            type="line", xref="paper", yref="y",
            x0=0.017, x1=0.067, y0=value, y1=value,
            line=dict(color=color, width=2.2, dash="dash"),
            layer="above",
        )
        fig.add_annotation(
            x=0.015, xref="paper", y=value, yref="y",
            text=name, showarrow=False, xanchor="left", yanchor="bottom",
            font=dict(color=color, size=10),
            bgcolor="rgba(13,17,23,0.80)", borderpad=1,
        )
        shown += 1
    return shown, clipped

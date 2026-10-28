"""Trend Daily SPX/ES: modulo di sola lettura indipendente dal NET GAMMA.
Non importa né modifica gamma_section, gamma_replay, GEX, livelli o Supabase.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


@st.cache_data(ttl=1800, show_spinner=False)
def _daily_bars():
    import yfinance as yf
    # ES future, coerente con la lettura del grafico future nel corso.
    data = yf.download("ES=F", period="1y", interval="1d",
                       auto_adjust=False, progress=False, threads=False)
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data[["Open", "High", "Low", "Close"]].dropna().copy()


def evaluate(frame):
    """Solo barre sicuramente chiuse. Nessuna previsione e nessun segnale trading."""
    if frame is None or len(frame) < 5:
        raise ValueError("Servono almeno cinque sedute Daily chiuse.")
    b = frame.iloc[-1]
    p = frame.iloc[-2]
    high, low = float(b["High"]), float(b["Low"])
    ph, pl = float(p["High"]), float(p["Low"])
    if high > ph and low > pl:
        direction = "RIALZISTA"
    elif high < ph and low < pl:
        direction = "RIBASSISTA"
    else:
        direction = "NEUTRA / MISTA"

    rng = high - low
    prev_rng = ph - pl
    close_prev = float(p["Close"])
    range_pct = 100 * rng / close_prev if close_prev else None
    shape = "ESPANSIONE" if rng > prev_rng else ("COMPRESSIONE" if rng < prev_rng else "INVARIATO")
    midpoint = (high + low) / 2
    last_close = float(b["Close"])
    midpoint_state = "SOPRA" if last_close > midpoint else ("SOTTO" if last_close < midpoint else "SUL LIVELLO")

    colors = []
    for _, row in frame.iloc[::-1].iterrows():
        op, cl = float(row["Open"]), float(row["Close"])
        color = "VERDE" if cl > op else ("ROSSA" if cl < op else "DOJI")
        if color == "DOJI" or (colors and color != colors[0]):
            break
        colors.append(color)
    return {
        "direction": direction, "range_pct": range_pct, "range_shape": shape,
        "midpoint": midpoint, "midpoint_state": midpoint_state,
        "same_color": len(colors), "bar_color": colors[0] if colors else "DOJI",
        "last_day": str(frame.index[-1].date()),
    }


def render(reference_day=None):
    st.subheader("Trend Daily — contesto SPX / ES")
    st.caption("Metodo Serafini: confronto delle ultime due barre Daily chiuse, range e baricentro. "
               "Analisi informativa: non modifica NET GAMMA, 0DTE, Weekly, Replay o GEX.")

    try:
        bars = _daily_bars()
        if bars.empty:
            st.info("Dati Daily ES non disponibili: analisi non calcolata.")
            return
        # Yahoo può includere la seduta corrente ancora in formazione.
        today_rome = reference_day or datetime.now(ZoneInfo("Europe/Rome")).date()
        index_dates = pd.to_datetime(bars.index).date
        bars = bars.loc[index_dates < today_rome]
        result = evaluate(bars)
    except Exception as exc:
        st.info("Analisi Daily temporaneamente non disponibile; gli altri moduli rimangono operativi.")
        st.caption(f"Dettaglio diagnostico: {type(exc).__name__}")
        return

    st.caption(f"Future ES · ultima candela chiusa: {result['last_day']} · sorgente Yahoo Finance (ES=F) · riferimento {today_rome:%d/%m/%Y}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Struttura Daily", result["direction"])
    c2.metric("Range", f"{result['range_pct']:.2f}%" if result["range_pct"] is not None else "N/D")
    c3.metric("Variazione del range", result["range_shape"])
    c4.metric("Barre stesso colore", str(result["same_color"]))
    st.caption(f"Baricentro ultima Daily: {result['midpoint']:,.2f} · "
               f"chiusura {result['midpoint_state'].lower()} il baricentro · "
               f"ultima barra {result['bar_color'].lower()}.")
    if result["same_color"] >= 3:
        st.warning("Tre o più candele consecutive dello stesso colore: verificare se sono "
                   "barre estese prima di applicare la regola di cautela di Serafini.")
    st.caption("Il riferimento dell'1,3% è descrittivo: non genera segnali. "
               "Espansione/compressione confronta i range delle due ultime sedute. "
               "Il baricentro mostrato appartiene all'ultima Daily, non identifica "
               "automaticamente una candela di impulso.")

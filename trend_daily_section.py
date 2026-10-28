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


def impulse_reference(frame, threshold_pct=1.3, body_fraction=0.60, lookback=20):
    """Criterio sperimentale dichiarato, distinto dalle regole validate.
    Cerca l'ultima candela di impulso PRECEDENTE all'ultima Daily chiusa.
    Range >= 1.3% del close anteriore, corpo >= 60% del range.
    """
    start = max(1, len(frame) - lookback - 1)
    for i in range(len(frame) - 2, start - 1, -1):
        candle = frame.iloc[i]
        previous_close = float(frame.iloc[i - 1]["Close"])
        hi, lo = float(candle["High"]), float(candle["Low"])
        op, cl = float(candle["Open"]), float(candle["Close"])
        bar_range = hi - lo
        if previous_close <= 0 or bar_range <= 0:
            continue
        if 100 * bar_range / previous_close < threshold_pct:
            continue
        if abs(cl - op) / bar_range < body_fraction:
            continue
        midpoint = (hi + lo) / 2
        subsequent = frame.iloc[i + 1:]
        lows = subsequent["Low"].astype(float)
        highs = subsequent["High"].astype(float)
        closes = subsequent["Close"].astype(float)
        bullish = cl > op
        if bullish:
            status = ("TENUTA" if (lows > midpoint).all() else
                      ("RECUPERATO" if closes.iloc[-1] > midpoint else "PERSO"))
        else:
            status = ("TENUTA" if (highs < midpoint).all() else
                      ("PERSO" if closes.iloc[-1] > midpoint else "RECUPERATO"))
        return {
            "day": str(pd.Timestamp(frame.index[i]).date()),
            "midpoint": midpoint,
            "side": "RIALZISTA" if bullish else "RIBASSISTA",
            "status": status,
            "bars": len(subsequent),
        }
    return None


def render(reference_day=None):
    st.subheader("Trend Daily — contesto SPX / ES")
    try:
        bars = _daily_bars()
        if bars.empty:
            st.info("Dati Daily ES non disponibili: analisi non calcolata.")
            return
        # Solo sedute chiuse anteriori alla data di riferimento.
        today_rome = reference_day or datetime.now(ZoneInfo("Europe/Rome")).date()
        index_dates = pd.to_datetime(bars.index).date
        bars = bars.loc[index_dates < today_rome]
        result = evaluate(bars)
        impulse = impulse_reference(bars)
    except Exception as exc:
        st.info("Analisi Daily temporaneamente non disponibile; gli altri moduli rimangono operativi.")
        st.caption(f"Dettaglio diagnostico: {type(exc).__name__}")
        return

    from html import escape
    count_color = ("#22c55e" if result["bar_color"] == "VERDE"
                   else "#ef4444" if result["bar_color"] == "ROSSA" else "#a1a1aa")
    range_text = f'{result["range_pct"]:.2f}%' if result["range_pct"] is not None else "N/D"
    baricentro_text = (
        f'Daily prec. {pd.Timestamp(result["last_day"]):%d/%m/%Y} — '
        f'Baricentro: {result["midpoint"]:,.2f} · '
        f'chiusura {result["midpoint_state"].lower()} il proprio baricentro · '
        f'candela {result["bar_color"].lower()}.'
    )
    if impulse is not None:
        is_bearish = impulse["side"] == "RIBASSISTA"
        if impulse["status"] == "TENUTA":
            explanation = ("nessuna candela successiva ha superato il baricentro"
                           if is_bearish else
                           "nessuna candela successiva è scesa sotto il baricentro")
        else:
            above = ((is_bearish and impulse["status"] == "PERSO")
                     or (not is_bearish and impulse["status"] == "RECUPERATO"))
            explanation = ("ultima chiusura Daily sopra il baricentro"
                           if above else "ultima chiusura Daily sotto il baricentro")
        impulse_text = (
            f'Candela di impulso {pd.Timestamp(impulse["day"]):%d/%m/%Y} '
            f'({impulse["side"].lower()}) — Baricentro: {impulse["midpoint"]:,.2f} · '
            f'{explanation} · sedute successive esaminate: {impulse["bars"]}.'
        )
    else:
        impulse_text = 'Candela di impulso precedente: nessuna individuata nelle ultime 20 sedute con i criteri sperimentali.'
    # Solo visualizzazione: la logica evaluate e i valori restano invariati.
    st.markdown(f"""
<style>
.spx-trend-card {{
  border:1px solid #34363c; border-radius:13px; padding:14px 18px;
  background:#090a0c; color:#f4f5f6;
}}
.spx-trend-grid {{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));align-items:center;}}
.spx-trend-kpi {{padding:6px 16px;border-left:1px solid #292b30;min-width:0;}}
.spx-trend-kpi:first-child {{border-left:0;padding-left:0;}}
.spx-trend-label {{font-size:.82rem;color:#b6bcc5;margin-bottom:6px;}}
.spx-trend-value {{font-size:1.22rem;font-weight:700;overflow-wrap:anywhere;}}
.spx-trend-foot {{
  border-top:1px solid #292b30;margin-top:12px;padding-top:10px;
  color:#cbd5e1;font-size:1rem;line-height:1.45;
}}
@media(max-width:750px) {{
  .spx-trend-grid {{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:10px;}}
  .spx-trend-kpi:nth-child(3) {{border-left:0;padding-left:0;}}
}}
</style>
<div class="spx-trend-card">
  <div class="spx-trend-grid">
    <div class="spx-trend-kpi"><div class="spx-trend-label">Struttura Daily</div><div class="spx-trend-value">{escape(result["direction"])}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Range</div><div class="spx-trend-value">{escape(range_text)}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Variazione del range</div><div class="spx-trend-value">{escape(result["range_shape"])}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Barre stesso colore</div><div class="spx-trend-value" style="color:{count_color}">{result["same_color"]}</div></div>
  </div>
  <div class="spx-trend-foot">{escape(baricentro_text)}</div>\n  <div class="spx-trend-foot">{escape(impulse_text)}</div>
</div>
""", unsafe_allow_html=True)
    st.caption("Candela di impulso: criterio sperimentale (range ≥1,3% della chiusura precedente, "
               "corpo ≥60% del range, ricerca nelle ultime 20 sedute). "
               "Tenuta = tutte le barre successive rispettano il baricentro; "
               "recuperato/perso = posizione dell'ultima chiusura dopo una violazione. "
               "Non è un segnale operativo validato.")
    if result["same_color"] >= 3:
        st.warning("Tre o più candele consecutive dello stesso colore: verificare se sono "
                   "barre estese prima di applicare la regola di cautela di Serafini.")

"""Trend Daily SPX/ES: stesso contratto Massive per Range Daily e struttura.
Non importa né modifica gamma_section, gamma_replay, GEX, livelli o Supabase.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


def massive_frame(rows, reference_day, contract=None):
    """Build closed-session OHLC candles from EXACT SAME Massive bars as range.

    Missing trade Open means color/impulse cannot be trusted, never invent it.
    """
    if not rows:
        raise ValueError("Non sono disponibili sessioni futures Massive.")
    if not isinstance(reference_day, date):
        raise ValueError("Data del contesto Daily non valida.")
    candles = {}
    for bar in rows:
        day = bar.get("day")
        if not isinstance(day, date) or day >= reference_day:
            continue
        if contract and bar.get("ticker") != contract:
            raise ValueError("I contratti Massive del Trend Daily non coincidono.")
        op, hi, lo, cl = (bar.get(k) for k in ("open", "hi", "lo", "close"))
        if any(v is None for v in (op, hi, lo, cl)):
            raise ValueError("OHLC Massive incompleto: manca Open, Trend Daily non calcolabile.")
        if not all(lo <= float(v) <= hi for v in (op, cl)) or float(hi) < float(lo):
            raise ValueError("OHLC Massive incoerente.")
        candles[day] = {"Open": float(op), "High": float(hi),
                        "Low": float(lo), "Close": float(cl)}
    if len(candles) < 5:
        raise ValueError("Servono almeno cinque sessioni Massive chiuse.")
    ordered = sorted(candles)
    data = pd.DataFrame([candles[d] for d in ordered],
                        index=pd.DatetimeIndex(ordered))
    return data



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
    last_close = float(b["Close"])
    range_pct = 100 * rng / last_close if last_close > 0 else None
    previous_range_pct = 100 * prev_rng / close_prev if close_prev > 0 else None
    if range_pct is None or previous_range_pct is None:
        shape = "N/D"
    elif abs(range_pct - previous_range_pct) < 1e-9:
        shape = "INVARIATO"
    else:
        shape = "ESPANSIONE" if range_pct > previous_range_pct else "COMPRESSIONE"
    midpoint = (high + low) / 2
    midpoint_delta_pct = 100 * (last_close / midpoint - 1) if midpoint > 0 else None
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
        "midpoint_delta_pct": midpoint_delta_pct, "last_close": last_close,
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


def render(reference_day=None, ticker="ES=F", market_name="SPX / ES",
           daily_bars=None, contract=None, daily_error=None):
    st.subheader(f"Trend Daily — contesto {market_name}")
    today_rome = reference_day or datetime.now(ZoneInfo("Europe/Rome")).date()
    try:
        bars = massive_frame(daily_bars, today_rome, contract=contract)
        result = evaluate(bars)
        impulse = impulse_reference(bars)
    except (ValueError, TypeError, KeyError):
        st.info("Trend Daily non disponibile: servono sessioni futures Massive con OHLC "
                "completi. Non utilizzo Yahoo come fonte alternativa.")
        if daily_error:
            st.caption(daily_error)
        return


    from html import escape
    count_color = ("#22c55e" if result["bar_color"] == "VERDE"
                   else "#ef4444" if result["bar_color"] == "ROSSA" else "#a1a1aa")
    # These metrics explain the candle's structural state, not a duplicate
    # of the Range Daily already shown above using the identical Massive bars.
    delta = result["midpoint_delta_pct"]
    delta_text = f"{delta:+.2f}%".replace(".", ",") if delta is not None else "N/D"
    gap_points = result["last_close"] - result["midpoint"]
    gap_text = f"{gap_points:+,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    center_text = f'{result["midpoint"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".")
    close_text = f'{result["last_close"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".")
    state_color = ("#56d49e" if result["midpoint_state"] == "SOPRA" else
                   "#ff676d" if result["midpoint_state"] == "SOTTO" else "#b6bcc5")
    baricentro_text = (
        f'Daily {pd.Timestamp(result["last_day"]):%d/%m/%Y} — '
        f'Close {close_text} · candela {result["bar_color"].lower()} · '
        f'distanza dal baricentro: {gap_text} pt ({delta_text}). '
        'Baricentro = (High + Low) / 2.'
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
    # Spiegazione descrittiva: nessuna modifica al rilevamento degli impulsi.
    if impulse is None:
        meaning = "Nessuna candela di impulso individuata con i criteri sperimentali."
    else:
        bearish = impulse["side"] == "RIBASSISTA"
        state = impulse["status"]
        if state == "TENUTA":
            meaning = ("Nelle sedute successive il prezzo è rimasto interamente sotto "
                       "il baricentro dell'impulso ribassista." if bearish else
                       "Nelle sedute successive il prezzo è rimasto interamente sopra "
                       "il baricentro dell'impulso rialzista.")
        elif bearish and state == "PERSO":
            meaning = ("Il prezzo ha superato il baricentro dell'impulso ribassista "
                       "e l'ultima Daily ha chiuso sopra quel livello. Può indicare "
                       "un indebolimento della pressione ribassista. Non significa "
                       "che tutte le sedute successive abbiano chiuso sopra.")
        elif bearish:
            meaning = ("Il prezzo è tornato sotto il baricentro dell'impulso ribassista "
                       "e l'ultima Daily ha chiuso sotto. Il recupero del livello "
                       "non è stato mantenuto; non implica che tutte le sedute "
                       "precedenti siano rimaste sotto.")
        elif state == "PERSO":
            meaning = ("Il prezzo è sceso sotto il baricentro dell'impulso rialzista "
                       "e l'ultima Daily ha chiuso sotto. Può segnalare un "
                       "indebolimento della spinta rialzista, ma non dimostra "
                       "un'inversione del trend.")
        else:
            meaning = ("Il prezzo ha attraversato il baricentro dell'impulso rialzista "
                       "ma l'ultima Daily ha chiuso sopra. Il livello è stato "
                       "recuperato; non significa che sia stato rispettato "
                       "in tutte le sedute successive.")
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
.spx-trend-sub {{font-size:.78rem;color:#a9b4c3;margin-top:6px;line-height:1.4;}}
.spx-trend-note {{border-top:1px solid #292b30;margin-top:9px;padding-top:9px;color:#9ca3af;font-size:.78rem;line-height:1.45;}}
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
    <div class="spx-trend-kpi"><div class="spx-trend-label">Chiusura vs baricentro</div><div class="spx-trend-value" style="color:{state_color}">{escape(result["midpoint_state"])}</div><div class="spx-trend-sub">{escape(delta_text)} dal baricentro</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Baricentro Daily</div><div class="spx-trend-value">{escape(center_text)}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Barre stesso colore</div><div class="spx-trend-value" style="color:{count_color}">{result["same_color"]}</div></div>
  </div>
  <div class="spx-trend-foot">{escape(baricentro_text)}</div>\n  <div class="spx-trend-foot">{escape(impulse_text)}</div>\n  <div class="spx-trend-foot">{escape(meaning)}</div>\n  <div class="spx-trend-note">Candela di impulso: criterio sperimentale (range ≥1,3% della chiusura precedente, corpo ≥60% del range, ricerca nelle ultime 20 sedute). Tenuta = tutte le barre successive rispettano il baricentro; recuperato/perso = posizione dell’ultima chiusura dopo una violazione. Non è un segnale operativo validato.</div>
</div>
""", unsafe_allow_html=True)
    st.caption(
        f"Fonte: Massive Futures {contract or 'N/D'} · 1session · "
        f"seduta chiusa {result['last_day']}. "
        "Stessa serie OHLC usata nel Range Daily precedente, senza dati Yahoo."
    )
    if result["same_color"] >= 3:
        st.warning("Tre o più candele consecutive dello stesso colore: verificare se sono "
                   "barre estese prima di applicare la regola di cautela di Serafini.")

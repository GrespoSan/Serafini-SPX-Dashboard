"""Trend Daily SPX/ES: stessa serie Massive per Range Daily e struttura.
Aggiunge il range della settimana ES precedente dalle stesse barre 1session.
Duplica soltanto la visualizzazione dei valori Edge GEX già verificati;
nessun nuovo download, ricalcolo GEX, accesso a Supabase o modifica Serafini.
"""
from datetime import date, datetime, timedelta
from html import escape
import math
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


def previous_week_range(frame, reference_day):
    """Previous COMPLETED Mon-Fri week, same Massive ES contract/1session.

    Anchor to the context date (not the last bar's calendar week); exclude
    the current week and future candles. Use only the existing validated
    massive_frame, not SPX cash, Yahoo, GEX or a new data request.
    Return N/D (None) for a potentially incomplete prior week (<3 sessions).
    Signed percentage = 100 * (last CLOSED Daily trade Close / level - 1).
    """
    if frame is None or frame.empty or not isinstance(reference_day, date):
        return None
    this_monday = reference_day - timedelta(days=reference_day.weekday())
    previous_monday = this_monday - timedelta(days=7)
    previous_friday = previous_monday + timedelta(days=4)
    mask = ((frame.index.date >= previous_monday)
            & (frame.index.date <= previous_friday)
            & (frame.index.dayofweek < 5))
    previous = frame.loc[mask]
    if len(previous) < 3:
        return None
    high = float(previous["High"].max())
    low = float(previous["Low"].min())
    close = float(frame.iloc[-1]["Close"])
    if not all(math.isfinite(x) and x > 0 for x in (high, low, close)) or high < low:
        return None
    return {
        "week_start": previous_monday, "week_end": previous_friday,
        "high": high, "low": low, "sessions": len(previous),
        "close": close, "close_day": frame.index[-1].date(),
        "from_low_pct": 100.0 * (close / low - 1.0),
        "from_high_pct": 100.0 * (close / high - 1.0),
    }



def weekly_exploration(frame, reference_day, previous_week):
    """Observed part of last week's ES range, using CLOSED Massive Daily bars.

    This cannot claim current-session intraday highs/lows. Monday/opening
    session must exist in the supplied series; never invent intraday data.
    """
    if previous_week is None or frame is None or frame.empty:
        return None
    monday = reference_day - timedelta(days=reference_day.weekday())
    mask = ((frame.index.date >= monday)
            & (frame.index.date < reference_day)
            & (frame.index.dayofweek < 5))
    current = frame.loc[mask].sort_index()
    if current.empty:
        return None
    opening = float(current.iloc[0]["Open"])
    high = float(current["High"].max())
    low = float(current["Low"].min())
    upper = previous_week["high"]
    lower = previous_week["low"]
    if not (all(math.isfinite(v) and v > 0 for v in
                (opening, high, low, upper, lower))
            and lower < upper and low <= opening <= high):
        return None
    return {
        "open": opening, "high": high, "low": low,
        "previous_high": upper, "previous_low": lower,
        "upper_touched": high >= upper,
        "lower_touched": low <= lower,
        "first_day": current.index[0].date(),
        "last_day": current.index[-1].date(),
        "sessions": len(current),
    }


def weekly_exploration_html(progress):
    """Single vertical weekly-range widget. Geometry is price proportional."""
    if progress is None:
        return ('<div class="spx-weekly-unavailable">Esplorazione N/D'
                '<div class="spx-trend-sub">Servono apertura e massimi/minimi '
                'delle sedute correnti già chiuse.</div></div>')

    upper, lower = progress["previous_high"], progress["previous_low"]
    span = upper - lower
    def pct(price):
        return max(0.0, min(100.0, 100.0 * (upper - price) / span))
    # Top of explored zone = current week's observed high, bottom = low.
    start = pct(progress["high"])
    end = pct(progress["low"])
    opening = pct(progress["open"])
    fill_height = max(0.0, end - start)
    edge_top = "#22c55e" if progress["upper_touched"] else "#777d87"
    edge_bottom = "#ef4444" if progress["lower_touched"] else "#777d87"
    top_label = "Toccato" if progress["upper_touched"] else "Non toccato"
    bottom_label = "Toccato" if progress["lower_touched"] else "Non toccato"
    def price(v):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (
        '<div class="spx-weekly-explore">'
        '<div class="spx-weekly-top"><span>MAX '
        + escape(price(upper)) + '</span><b style="color:' + edge_top
        + '">' + top_label + '</b></div>'
        '<div class="spx-weekly-body">'
        '<div class="spx-weekly-track" role="img" aria-label="'
        + escape('Range precedente ' + price(lower) + '–' + price(upper)
                 + ', apertura ' + price(progress["open"])
                 + ', minimo corrente ' + price(progress["low"])
                 + ', massimo corrente ' + price(progress["high"]), quote=True)
        + '">'
        '<div class="spx-weekly-fill" style="top:' + f'{start:.4f}'
        + '%;height:' + f'{fill_height:.4f}' + '%"></div>'
        '<div class="spx-weekly-open" style="top:' + f'{opening:.4f}'
        + '%"></div>'
        '<div class="spx-weekly-edge top" style="background:'
        + ('#22c55e' if progress["upper_touched"] else 'transparent')
        + '"></div><div class="spx-weekly-edge bottom" style="background:'
        + ('#ef4444' if progress["lower_touched"] else 'transparent')
        + '"></div></div>'
        '<div class="spx-weekly-legend">'
        '<span>Apertura <b>' + escape(price(progress["open"])) + '</b></span>'
        '<span>Max corrente <b>' + escape(price(progress["high"])) + '</b></span>'
        '<span>Min corrente <b>' + escape(price(progress["low"])) + '</b></span>'
        '</div></div>'
        '<div class="spx-weekly-bottom"><span>MIN '
        + escape(price(lower)) + '</span><b style="color:'
        + edge_bottom + '">' + bottom_label + '</b></div>'
        '<div class="spx-trend-sub">Esplorazione fino alla Daily chiusa '
        + progress["last_day"].strftime("%d/%m/%Y")
        + ' · non intraday/live</div></div>'
    )


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
           daily_bars=None, contract=None, daily_error=None,
           modeled_gex=None):
    st.subheader(f"Trend Daily — contesto {market_name}")
    # One compact GEX card next to the four Daily KPIs, not a second header.
    # Reuse validated Edge values only: no Cboe request, new Gamma calculation,
    # cached stale value, or dependency on Massive Daily OHLC.
    gex_card = ""
    gex_caption = ""
    if modeled_gex is not None:
        try:
            from edge_daily_gex import compact_gex_kpi_html, compact_gex_caption
            gex_card = compact_gex_kpi_html(modeled_gex)
            gex_caption = compact_gex_caption(modeled_gex)
        except (ValueError, TypeError, KeyError):
            # A missing or malformed optional GEX must never block Daily.
            gex_card = ""
            gex_caption = ""
    today_rome = reference_day or datetime.now(ZoneInfo("Europe/Rome")).date()
    try:
        bars = massive_frame(daily_bars, today_rome, contract=contract)
        result = evaluate(bars)
        impulse = impulse_reference(bars)
        prior_week = previous_week_range(bars, today_rome)
        weekly_progress = weekly_exploration(bars, today_rome, prior_week)
    except (ValueError, TypeError, KeyError):
        # If OHLC is missing, a successfully verified Cboe GEX remains
        # available in its own small dark card; no stale or invented data.
        if gex_card:
            st.markdown(
                '<div style="background:#090a0c;border:1px solid #34363c;'
                'border-radius:13px;padding:14px 18px;color:#f4f5f6;'
                'max-width:370px">'
                '<style>.spx-trend-gex-label{color:#b6bcc5;font-size:.75rem;'
                'margin-top:6px}.spx-trend-gex-value{font-size:1.15rem;'
                'font-weight:700}</style>'
                + gex_card + '</div>',
                unsafe_allow_html=True)
            st.caption(gex_caption)
        st.info("Trend Daily non disponibile: servono sessioni futures Massive con OHLC "
                "completi. Non utilizzo Yahoo come fonte alternativa.")
        if daily_error:
            st.caption(daily_error)
        return


    count_color = ("#22c55e" if result["bar_color"] == "VERDE"
                   else "#ef4444" if result["bar_color"] == "ROSSA" else "#a1a1aa")
    # These metrics explain the candle's structural state, not a duplicate
    # of the Range Daily already shown above using the identical Massive bars.
    delta = result["midpoint_delta_pct"]
    delta_text = f"{delta:+.2f}%".replace(".", ",") if delta is not None else "N/D"
    gap_points = result["last_close"] - result["midpoint"]
    gap_text = f"{gap_points:+,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    close_text = f'{result["last_close"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".")
    state_color = ("#56d49e" if result["midpoint_state"] == "SOPRA" else
                   "#ff676d" if result["midpoint_state"] == "SOTTO" else "#b6bcc5")
    direction_color = ("#ff676d" if result["direction"] == "RIBASSISTA" else
                       "#56d49e" if result["direction"] == "RIALZISTA" else "#b6bcc5")
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
    # In evidenza la TENUTA dell'ultimo impulso, non il baricentro della
    # singola candela Daily: quest'ultimo resta nei dettagli.
    if impulse is None:
        impulse_label = "Baricentro impulso"
        impulse_value = "N/D"
        impulse_sub = "Nessun impulso nelle ultime 20 sedute"
        impulse_color = "#b6bcc5"
    else:
        impulse_label = (
            f"Baricentro impulso {pd.Timestamp(impulse['day']):%d/%m/%Y}")
        impulse_value = (f"{impulse['midpoint']:,.2f}"
                         .replace(",", "X").replace(".", ",").replace("X", "."))
        impulse_sub = (
            f"{impulse['side'].capitalize()} · {impulse['status']} · "
            f"{impulse['bars']} "
            f"{'seduta' if impulse['bars'] == 1 else 'sedute'}")
        impulse_color = ("#56d49e" if impulse["side"] == "RIALZISTA"
                         else "#ff676d")
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
    # Sesto KPI descrittivo: Weekly COMPLETATA precedente (non settimana
    # corrente in corso), calcolata sulla stessa serie ES Massive 1session.
    def _it_price(value):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    week_html = weekly_exploration_html(weekly_progress)
    previous_monday = today_rome - timedelta(days=today_rome.weekday() + 7)
    previous_friday = previous_monday + timedelta(days=4)
    weekly_label = (f"Esplorazione Weekly · {previous_monday:%d}–"
                    f"{previous_friday:%d/%m/%Y}")
    weekly_card = (
        '<div class="spx-trend-kpi spx-trend-weekly">'
        '<div class="spx-trend-label">' + escape(weekly_label) + '</div>'
        + week_html + '</div>')
    # Il riferimento è la CHIUSURA Daily, non una quotazione intraday live.
    # I dati GEX sono indipendenti e il numero di KPI resta dinamico.
    columns_css = ("repeat(4,minmax(0,1fr)) minmax(190px,1.08fr) minmax(200px,1.12fr)"
                   if gex_card
                   else "repeat(4,minmax(0,1fr)) minmax(200px,1.12fr)")
    gex_source = (f'<div class="spx-trend-gex-source">{escape(gex_caption)}</div>'
                  if gex_card else "")
    st.html(f"""
<style>
.spx-trend-card {{
  border:1px solid #34363c; border-radius:13px; padding:14px 18px;
  background:#090a0c; color:#f4f5f6;
}}
.spx-trend-grid {{display:grid;grid-template-columns:{columns_css};align-items:stretch;}}
.spx-trend-kpi {{padding:6px 16px;border-left:1px solid #292b30;min-width:0;}}
.spx-trend-kpi:first-child {{border-left:0;padding-left:0;}}
.spx-trend-label {{font-size:.82rem;color:#b6bcc5;margin-bottom:6px;}}
.spx-trend-value {{font-size:1.22rem;font-weight:700;overflow-wrap:anywhere;}}
.spx-trend-sub {{font-size:.78rem;color:#a9b4c3;margin-top:6px;line-height:1.4;}}
.spx-trend-gex-label {{font-size:.74rem;color:#b6bcc5;margin-top:7px;line-height:1.25;}}
.spx-trend-gex-value {{font-size:1.08rem;font-weight:700;line-height:1.35;overflow-wrap:anywhere;}}
.spx-trend-gex .spx-trend-label {{font-weight:600;}}
.spx-trend-gex-source {{padding:7px 0 9px;color:#9ca3af;font-size:.72rem;line-height:1.4;}}
.spx-trend-weekly .spx-trend-label {{font-weight:600;}}
.spx-weekly-explore {{max-width:310px;}}
.spx-weekly-top,.spx-weekly-bottom {{display:flex;justify-content:space-between;
  gap:8px;font-size:.80rem;color:#cbd5e1;margin:4px 0;}}
.spx-weekly-top b,.spx-weekly-bottom b {{font-size:.76rem;white-space:nowrap;}}
.spx-weekly-body {{display:flex;align-items:stretch;gap:12px;margin:7px 0;}}
.spx-weekly-track {{height:132px;width:50px;min-width:50px;position:relative;
  border:1px solid #8c939d;background:#090a0c;box-sizing:border-box;}}
.spx-weekly-fill {{position:absolute;left:0;right:0;background:#c6cbd1;opacity:.90;}}
.spx-weekly-open {{position:absolute;left:0;right:0;height:4px;
  background:#6b7280;transform:translateY(-50%);z-index:2;}}
.spx-weekly-edge {{position:absolute;left:-1px;right:-1px;height:5px;z-index:3;}}
.spx-weekly-edge.top {{top:-1px;}}
.spx-weekly-edge.bottom {{bottom:-1px;}}
.spx-weekly-legend {{display:flex;flex-direction:column;justify-content:center;
  gap:10px;font-size:.78rem;line-height:1.3;color:#b6bcc5;}}
.spx-weekly-legend b {{color:#f4f5f6;font-weight:600;}}
.spx-weekly-unavailable {{padding:12px 0;color:#cbd5e1;}}
@media(max-width:1300px) {{
  .spx-trend-grid {{grid-template-columns:repeat(3,minmax(0,1fr));row-gap:10px;}}
  .spx-trend-kpi:nth-child(3n+1) {{border-left:0;padding-left:0;}}
}}

.spx-trend-note {{border-top:1px solid #292b30;margin-top:9px;padding-top:9px;color:#9ca3af;font-size:.78rem;line-height:1.45;}}
.spx-trend-foot {{
  border-top:1px solid #292b30;margin-top:12px;padding-top:10px;
  color:#cbd5e1;font-size:1rem;line-height:1.45;
}}
.spx-trend-impulse {{
  border-top:1px solid #292b30;margin-top:12px;padding-top:12px;
  color:#f4f5f6;font-size:.96rem;line-height:1.5;
}}
.spx-trend-impulse b {{font-weight:700;}}

@media(max-width:750px) {{
  .spx-trend-grid {{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:10px;}}
  .spx-trend-kpi:nth-child(odd) {{border-left:0;padding-left:0;}}
}}
</style>
<div class="spx-trend-card">
  <div class="spx-trend-grid">
    <div class="spx-trend-kpi"><div class="spx-trend-label">Struttura Daily</div><div class="spx-trend-value" style="color:{direction_color}">{escape(result["direction"])}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Chiusura vs baricentro Daily</div><div class="spx-trend-value" style="color:{state_color}">{escape(result["midpoint_state"])}</div><div class="spx-trend-sub">{escape(delta_text)} dal baricentro</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">{escape(impulse_label)}</div><div class="spx-trend-value" style="color:{impulse_color}">{escape(impulse_value)}</div><div class="spx-trend-sub">{escape(impulse_sub)}</div></div>
    <div class="spx-trend-kpi"><div class="spx-trend-label">Barre stesso colore</div><div class="spx-trend-value" style="color:{count_color}">{result["same_color"]}</div></div>
    {gex_card}
    {weekly_card}
  </div>
  {gex_source}
  <div class="spx-trend-impulse">{escape(impulse_text)}</div>
  <div class="spx-trend-foot">{escape(baricentro_text)}</div>\n  <div class="spx-trend-foot">{escape(meaning)}</div>\n  <div class="spx-trend-note">Candela di impulso: criterio sperimentale (range ≥1,3% della chiusura precedente, corpo ≥60% del range, ricerca nelle ultime 20 sedute). Tenuta = tutte le barre successive rispettano il baricentro; recuperato/perso = posizione dell’ultima chiusura dopo una violazione. Non è un segnale operativo validato.</div>
</div>
""")
    st.caption(
        f"Fonte: Massive Futures {contract or 'N/D'} · 1session · "
        f"seduta chiusa {result['last_day']}. "
        "Stessa serie OHLC usata nel Range Daily precedente, senza dati Yahoo."
    )
    if result["same_color"] >= 3:
        st.warning("Tre o più candele consecutive dello stesso colore: verificare se sono "
                   "barre estese prima di applicare la regola di cautela di Serafini.")

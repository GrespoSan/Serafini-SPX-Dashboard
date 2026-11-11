"""SOLA SEZIONE GEX EDGE — Laboratorio sensibilita prezzo (what-if).

Usa esclusivamente la curva GEX teorica GIA' ARCHIVIATA da Edge:
non reinventa Black-Scholes, non aggiorna OI/IV, non ricalcola le Wall,
non modifica Serafini NET GAMMA o i dati ufficiali.
"""
import math
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st


def scenario_gex(curve, spx_spot):
    """Interpolazione lineare DENTRO la curva teorica originale, mai extrapolazione.

    Restituisce l'esposizione NET GEX in USD della curva congelata.
    Non ricostruisce gamma per singola opzione (necessaria per nuove Wall).
    """
    spot = float(spx_spot)
    if not math.isfinite(spot):
        raise ValueError("Prezzo scenario non valido")
    if not isinstance(curve, list) or len(curve) < 2:
        return None
    points = []
    for row in curve:
        try:
            x = float(row["spot_spx"])
            y = float(row["net_gex"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        points.append((x, y))
    points.sort()
    if any(x0 >= x1 for (x0, _), (x1, _) in zip(points, points[1:])):
        return None
    if not points[0][0] <= spot <= points[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= spot <= x1:
            fraction = (spot - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return points[-1][1] if spot == points[-1][0] else None


def _signed_metric_color(value):
    """Colore semantico del valore GEX, senza confondere segno e delta."""
    number = float(value)
    if not math.isfinite(number):
        return "inherit"
    if number > 0:
        return "#22b573"
    if number < 0:
        return "#e64b4b"
    return "inherit"


def _signed_gex_metric(column, label, value, *, suffix="mld USD"):
    """Colora soltanto il numero, mantenendo etichette e unità leggibili."""
    amount = float(value) / 1e9
    color = _signed_metric_color(value)
    formatted = f"{amount:+.2f}" if amount != 0 else "0.00"
    column.markdown(
        '<div style="min-height:5rem">'
        f'<div style="font-size:.875rem;line-height:1.5rem;opacity:.85">'
        f'{escape(str(label))}</div>'
        f'<div style="font-size:2rem;line-height:2.45rem;font-weight:400;'
        f'color:{color};overflow-wrap:anywhere">'
        f'{escape(formatted + " " + suffix)}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _gex_reference_times(download_clock, valuation):
    """Date verificabili: la valutazione teorica NON è l'ora del download.

    Non attribuire il fuso Roma a timestamp naive. Se manca il tempo
    effettivo di elaborazione del motore, NON inventarlo.
    """
    if isinstance(download_clock, datetime) and download_clock.tzinfo is not None:
        snapshot = download_clock.astimezone(ZoneInfo("Europe/Rome")).strftime(
            "%d/%m/%Y %H:%M:%S Roma")
    else:
        snapshot = "N/D"
    model = "N/D"
    if valuation:
        try:
            parsed = datetime.fromisoformat(str(valuation).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                model = parsed.astimezone(ZoneInfo("Europe/Rome")).strftime(
                    "%d/%m/%Y %H:%M Roma")
            else:
                model = parsed.strftime("%d/%m/%Y %H:%M") + " (fuso non dichiarato)"
        except (TypeError, ValueError, OverflowError):
            pass
    return snapshot, model


def show_price_lab(edge, es_basis, last_es=None, *, key="edge_scenario",
                   download_clock=None, slot=None):
    """Laboratorio collassato, senza richieste network o scritture.

    Curva scenario = stessi OI/IV/valutazione del report Edge. Conversione
    ES->SPX con offset congelato e dichiarato. Nessuna previsione intraday.
    """
    with st.expander("LABORATORIO GEX — scenario prezzo (sperimentale)",
                     expanded=False):
        snapshot_time, valuation_time = _gex_reference_times(
            download_clock, edge.get("valuation"))
        slot_text = f" · Replay {slot}" if slot else ""
        st.markdown(
            f"**Fotografia Cboe del calcolo GEX:** {snapshot_time}{slot_text}  "
            f"\\n**Valutazione teorica del modello:** {valuation_time}"
        )
        st.caption(
            "Lo scenario viene interpolato dalla curva GEX salvata per "
            "questa fotografia. Cambiare prezzo NON acquisisce nuove opzioni "
            "e non aggiorna la data di valutazione; l'orario effettivo di "
            "esecuzione del motore non è archiviato separatamente."
        )
        st.caption(
            "Ricerca WHAT-IF, non un nuovo indicatore né un segnale di trading. "
            "Si usa la curva teorica già calcolata dal motore Edge sulla "
            "stessa fotografia Cboe, con OI, IV e scadenza congelati. "
            "NON si ricostruiscono nuove Put/Call Wall o Gamma Flip."
        )
        curve = edge.get("curve") or []
        if len(curve) < 2:
            st.info("La fotografia non contiene una curva teorica GEX "
                    "sufficiente: nessuna interpolazione possibile.")
            return
        try:
            points = sorted((float(row["spot_spx"]), float(row["net_gex"]))
                            for row in curve)
            if (not all(math.isfinite(x) and math.isfinite(y) for x, y in points)
                    or any(x >= nx for (x, _), (nx, _) in zip(points, points[1:]))):
                raise ValueError("Curva non monotona")
            basis = float(es_basis)
            cash_spot = float(edge["spot_spx"])
            actual_gex = float(edge["net_gex"])
            if not all(math.isfinite(x) for x in (basis, cash_spot, actual_gex)):
                raise ValueError("Prezzi o GEX non validi")
        except (TypeError, ValueError, KeyError, OverflowError):
            st.warning("Curva non verificabile; laboratorio disabilitato.")
            return
        lower = points[0][0] + basis
        upper = points[-1][0] + basis
        observed_es = (last_es.get("price") if isinstance(last_es, dict)
                       else None)
        observed_valid = False
        if observed_es is not None:
            try:
                observed_es = float(observed_es)
                observed_valid = math.isfinite(observed_es) and observed_es > 0
            except (TypeError, ValueError, OverflowError):
                observed_valid = False
        scenario_default = cash_spot + basis
        if observed_valid and lower <= observed_es <= upper:
            scenario_default = observed_es
        elif observed_valid:
            st.caption(
                f"L'ultima ES 5m verificata ({observed_es:,.2f}) è fuori "
                "dalla curva salvata: nessuna extrapolazione. "
                "Seleziona manualmente un prezzo dentro la curva."
            )
        if not (lower <= scenario_default <= upper):
            st.warning("Il prezzo base SPX è fuori dalla curva: "
                       "impossibile inizializzare una simulazione affidabile.")
            return
        value = st.number_input(
            "Prezzo future ES ipotetico",
            min_value=float(lower), max_value=float(upper),
            value=float(scenario_default), step=0.25, format="%.2f",
            key=key,
            help="Scenario, non prezzo ES live. L'offset SPX→ES resta fisso."
        )
        hypothetical_spx = float(value) - basis
        model_at_scenario = scenario_gex(curve, hypothetical_spx)
        baseline_curve = scenario_gex(curve, cash_spot)
        if model_at_scenario is None or baseline_curve is None:
            st.info("Prezzo fuori dai punti della curva: "
                    "nessun confronto teorico affidabile calcolabile.")
            return
        a, b, c = st.columns(3)
        _signed_gex_metric(a, "NET GEX originale", actual_gex)
        _signed_gex_metric(b, "NET GEX teorico allo scenario",
                           model_at_scenario)
        # Confronto della STESSA curva, non originale vs interpolazione:
        # non confondere una discrepanza numerica con un effetto-prezzo.
        _signed_gex_metric(c, "Δ teorico dalla curva al cash SPX",
                           model_at_scenario - baseline_curve, suffix="mld")
        chart = go.Figure()
        chart.add_trace(go.Scatter(
            x=[x+basis for x, _ in points],
            y=[y/1e9 for _, y in points],
            mode="lines", name="Curva Edge archiviata"))
        chart.add_vline(x=cash_spot+basis, line_dash="dash",
                        line_color="#a0a0a0")
        chart.add_vline(x=float(value), line_dash="dot",
                        line_color="#eabf4a")
        chart.add_trace(go.Scatter(
            x=[float(value)], y=[model_at_scenario/1e9],
            mode="markers", name="Scenario teorico",
            marker=dict(size=10, color="#eabf4a")))
        chart.add_hline(y=0, line_dash="dash", line_color="#777777")
        chart.update_layout(
            title="Sensibilità teorica GEX alla variazione del prezzo",
            xaxis_title="Prezzo ES ipotetico (SPX + differenziale fisso)",
            yaxis_title="NET GEX teorico · mld USD / 1%",
            height=390)
        st.plotly_chart(chart, width="stretch")
        st.caption(
            f"Input scenario SPX = ES {float(value):,.2f} − base {basis:+.2f} "
            f"= {hypothetical_spx:,.2f}. "
            "NET GEX dello scenario ottenuto per interpolazione lineare "
            "tra punti della curva Edge archiviata. Il delta usa la stessa "
            "curva interpolata allo SPX cash, evitando differenze puramente "
            "numeriche rispetto al valore Edge originale; NON un nuovo download, "
            "NON una rivalutazione completa di ogni contratto, NON nuove Wall "
            "o Flip e NON una misura di posizioni dealer osservate."
        )

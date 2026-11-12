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


def _add_price_reference_markers(chart, *, original_es, original_gex_on_curve,
                                 scenario_es, scenario_gex_on_curve):
    """Due riferimenti sullo stesso profilo GEX: prima originale, poi scenario.

    Il pallino grigio usa la stessa interpolazione del delta numerico, non
    inventa un nuovo GEX o un prezzo da una fotografia successiva.
    """
    chart.add_trace(go.Scatter(
        x=[float(original_es)], y=[float(original_gex_on_curve) / 1e9],
        mode="markers", name="Originale (curva)",
        marker=dict(size=10, color="#a0a0a0"),
        hovertemplate=(
            "Originale sulla curva Edge<br>"
            "ES %{x:,.2f}<br>NET GEX %{y:+.2f} mld USD<extra></extra>"
        ),
    ))
    chart.add_trace(go.Scatter(
        x=[float(scenario_es)], y=[float(scenario_gex_on_curve) / 1e9],
        mode="markers", name="Scenario teorico",
        marker=dict(size=10, color="#eabf4a"),
        hovertemplate=(
            "Scenario teorico<br>"
            "ES %{x:,.2f}<br>NET GEX %{y:+.2f} mld USD<extra></extra>"
        ),
    ))


def curve_extrema(curve, es_basis):
    """Sampled extrema of the existing saved/modelled Edge curve (no fitting)."""
    try:
        points = [(float(row["spot_spx"]), float(row["net_gex"]))
                  for row in curve]
        basis = float(es_basis)
        if len(points) < 2 or not math.isfinite(basis) or not all(
                math.isfinite(x) and math.isfinite(y) for x, y in points):
            return None
        high = max(points, key=lambda point: point[1])
        low = min(points, key=lambda point: point[1])
        return {"max": {"es": high[0] + basis, "spx": high[0],
                        "net_gex": high[1]},
                "min": {"es": low[0] + basis, "spx": low[0],
                        "net_gex": low[1]}}
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _add_delayed_es_marker(chart, *, es_price, gex_on_curve):
    """Visual-only third marker, independent of original and scenario."""
    chart.add_vline(x=float(es_price), line_dash="dash",
                    line_color="#4cc6e5", line_width=1.5)
    chart.add_trace(go.Scatter(
        x=[float(es_price)], y=[float(gex_on_curve) / 1e9],
        mode="markers", name="Ultimo ES disponibile",
        marker=dict(size=11, color="#4cc6e5",
                    line=dict(color="#10222c", width=1.2)),
        hovertemplate=(
            "Ultimo ES disponibile (non live)<br>"
            "ES %{x:,.2f}<br>"
            "NET GEX sulla curva fissa %{y:+.2f} mld USD<extra></extra>"
        ),
    ))


def show_price_lab(edge, es_basis, last_es=None, *, key="edge_scenario",
                   download_clock=None, slot=None, source_kind="Replay",
                   current_es=None, history_options=(), history_loader=None):
    """Laboratorio aperto di default, senza richieste network o scritture.

    Curva scenario = stessi OI/IV/valutazione del report Edge. Conversione
    ES->SPX con offset congelato e dichiarato. Nessuna previsione intraday.
    """
    with st.expander("Curva teorica GEX Edge — scenario prezzo (sperimentale)",
                     expanded=True):
        snapshot_time, valuation_time = _gex_reference_times(
            download_clock, edge.get("valuation"))
        slot_text = f" · {source_kind} {slot}" if slot else f" · {source_kind}"
        st.markdown(
            f"**Fotografia Cboe del calcolo GEX · {source_kind.upper()}** "
            f"{slot or ''}: {snapshot_time}"
        )
        st.markdown(
            f"**Valutazione modello GEX · {source_kind.upper()} "
            f"{slot or ''}: {valuation_time}**"
        )
        st.caption(
            "Unica curva GEX 0DTE della fotografia più recente, Replay oppure "
            "download Cboe: nessuna fusione di fotografie. La valutazione del "
            "modello non coincide necessariamente con l'ora del download. "
            "Lo scenario è WHAT-IF: OI, IV e scadenza congelati; non aggiorna "
            "Wall, Flip o posizioni dei dealer e non genera segnali."
        )
        if edge.get("quality_sufficient") is not True:
            st.warning(
                "Copertura del modello Edge insufficiente o non verificabile: "
                "curva soltanto diagnostica. Non è un errore del download Cboe."
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
        # Prices are the X-axis endpoints, NOT the NET GEX Y-axis peaks.
        min_col, max_col = st.columns(2)
        min_col.metric("Prezzo minimo ES della curva", f"{lower:,.2f}")
        min_col.caption(
            f"Limite teorico inferiore · SPX {points[0][0]:,.2f} · "
            f"NET GEX {points[0][1]/1e9:+.2f} mld"
        )
        max_col.metric("Prezzo massimo ES della curva", f"{upper:,.2f}")
        max_col.caption(
            f"Limite teorico superiore · SPX {points[-1][0]:,.2f} · "
            f"NET GEX {points[-1][1]/1e9:+.2f} mld"
        )

        a, b, c = st.columns(3)
        _signed_gex_metric(a, "NET GEX originale", actual_gex)
        _signed_gex_metric(b, "NET GEX teorico allo scenario",
                           model_at_scenario)
        # Confronto della STESSA curva, non originale vs interpolazione:
        # non confondere una discrepanza numerica con un effetto-prezzo.
        _signed_gex_metric(c, "Δ teorico dalla curva al cash SPX",
                           model_at_scenario - baseline_curve, suffix="mld")
        # Historical Replay is an OPTIONAL comparison of a distinct curve;
        # it never changes the main triptych, GEX KPIs or active latest source.
        historical_curve = None
        historical_slot = None
        if history_options and history_loader is not None:
            with st.expander("Confronto Replay storico (facoltativo)",
                             expanded=False):
                historical_slot = st.selectbox(
                    "Rilevamento Replay da confrontare nella curva",
                    ["Nessuno"] + list(history_options),
                    key=f"{key}_historical_curve",
                )
                if historical_slot != "Nessuno":
                    try:
                        earlier_edge = history_loader(historical_slot)
                        if (earlier_edge.get("mode") == edge.get("mode") == "0DTE"
                                and earlier_edge.get("expiries") == edge.get("expiries")
                                and earlier_edge.get("model_version") == edge.get("model_version")
                                and earlier_edge.get("quality_sufficient") is True
                                and edge.get("quality_sufficient") is True):
                            historical_curve = earlier_edge.get("curve") or None
                        else:
                            st.warning("Confronto sospeso: qualità, scadenza o "
                                       "versione del modello non coerenti.")
                    except (RuntimeError, ValueError, TypeError, KeyError,
                            OverflowError, OSError) as exc:
                        st.warning(f"Replay storico non verificabile: {exc}")

        chart = go.Figure()
        chart.add_trace(go.Scatter(
            x=[x+basis for x, _ in points],
            y=[y/1e9 for _, y in points],
            mode="lines", name="Curva Edge archiviata"))
        if historical_curve:
            try:
                old_points = sorted((float(row["spot_spx"]) + basis,
                                     float(row["net_gex"]) / 1e9)
                                    for row in historical_curve)
                if all(math.isfinite(x) and math.isfinite(y)
                       for x, y in old_points):
                    chart.add_trace(go.Scatter(
                        x=[x for x, _ in old_points],
                        y=[y for _, y in old_points],
                        mode="lines", name=f"Replay {historical_slot} · confronto",
                        line=dict(dash="dash")))
                    st.caption(
                        f"Linea tratteggiata: Replay storico {historical_slot}. "
                        "La curva corrente, i KPI e lo scenario non cambiano."
                    )
            except (ValueError, TypeError, KeyError, OverflowError):
                pass
        chart.add_vline(x=cash_spot+basis, line_dash="dash",
                        line_color="#a0a0a0")
        chart.add_vline(x=float(value), line_dash="dot",
                        line_color="#eabf4a")
        _add_price_reference_markers(
            chart,
            original_es=cash_spot + basis,
            original_gex_on_curve=baseline_curve,
            scenario_es=float(value),
            scenario_gex_on_curve=model_at_scenario,
        )
        # Third reference uses a separate delayed ES 5-minute CLOSED bar.
        # Never invent a live quote or change the original Edge curve.
        if isinstance(current_es, dict):
            try:
                delayed_price = float(current_es["price"])
                delayed_time = str(current_es["clock"])
                delayed_source = str(current_es["source"])
                delayed_age = float(current_es["age_minutes"])
                if not (math.isfinite(delayed_price) and delayed_price > 0
                        and math.isfinite(delayed_age) and delayed_age >= 0):
                    raise ValueError("Prezzo corrente non valido")
                projected = scenario_gex(curve, delayed_price - basis)
                if projected is not None:
                    _add_delayed_es_marker(
                        chart, es_price=delayed_price, gex_on_curve=projected)
                    st.caption(
                        f"**Ultimo ES disponibile (non live):** "
                        f"{delayed_price:,.2f} · candela 5m chiusa "
                        f"{delayed_time} Roma · fonte {delayed_source} "
                        f"· ritardo {delayed_age:.0f} min. "
                        "Il punto azzurro è interpolato sulla curva GEX "
                        "della fotografia, non è un nuovo calcolo dealer.")
                else:
                    st.caption(
                        f"**Ultimo ES disponibile (non live):** "
                        f"{delayed_price:,.2f} · candela 5m chiusa "
                        f"{delayed_time} Roma · {delayed_source} "
                        f"· ritardo {delayed_age:.0f} min. "
                        "Fuori dall'intervallo della curva: nessun punto GEX "
                        "o estrapolazione inventati.")
                    chart.add_vline(x=delayed_price, line_dash="dash",
                                    line_color="#4cc6e5")
            except (TypeError, ValueError, KeyError, OverflowError):
                st.caption("Ultimo prezzo ES non verificabile: riferimento non disegnato.")
        else:
            st.caption(
                "Ultimo prezzo ES non disponibile per questa data: "
                "nessun riferimento fittizio. Lo scenario resta indipendente.")
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

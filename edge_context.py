"""Read-only visualizer of exported Edge GEX results. No Edge or Serafini calculations imported."""
import json
import math
from datetime import datetime
import plotly.graph_objects as go
import streamlit as st

MAX_BYTES = 3_000_000

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Valore numerico Edge non valido.")
    return float(value)

def read_report(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("Report Edge troppo grande.")
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("JSON Edge non leggibile.") from None
    if not isinstance(doc, dict) or doc.get("schema") != "edge-gex-context-v1" or doc.get("symbol") != "SPX":
        raise ValueError("Caricare soltanto un report SPX esportato dall'app Edge.")
    if not isinstance(doc.get("expiries"), list) or not isinstance(doc.get("rows"), list):
        raise ValueError("Struttura report Edge incompleta.")
    if len(doc["rows"]) > 2000 or len(doc.get("curve", [])) > 2000:
        raise ValueError("Troppe righe nel report.")
    datetime.fromisoformat(doc["timestamp"].replace("Z", "+00:00"))
    for field in ("spot_spx", "net_gex", "gross_gex"):
        _number(doc[field])
    for row in doc["rows"]:
        for field in ("strike_spx", "call_gex", "put_gex", "net_gex"):
            _number(row[field])
    return doc

def show(week):
    with st.expander("EDGE GEX — Contesto separato (solo confronto)", expanded=False):
        st.caption("Motore Edge indipendente. I dati qui visualizzati provengono dal report Edge JSON; nessuna formula Serafini, selezione C1/P1 o verdetto viene modificata.")
        report_file = st.file_uploader("Carica report Edge per SPX (JSON)", type=["json"], key="edge_context_json")
        if not report_file:
            st.info("Apri Edge, seleziona la stessa fotografia e scadenza, poi usa «Scarica confronto Edge per SPX (JSON)». Carica qui quel file.")
            return
        try:
            e = read_report(report_file.getvalue())
        except (ValueError, KeyError, TypeError) as error:
            st.error(str(error))
            return
        ctx = st.session_state.get(f"gamma_compare_context_{week}")
        st.caption(f"EDGE | timestamp CBOE: {e['timestamp']} | scadenze: {', '.join(e['expiries'])} | SHA256 fotografia: {e['sha256'][:12]}")
        if ctx:
            edge_day = e["expiries"][0] if len(e["expiries"]) == 1 else None
            if edge_day and str(ctx.get("day")) != edge_day:
                st.warning("Scadenza Edge diversa dalla data selezionata in Serafini: confronto non omogeneo.")
            st.caption("Controllare anche l'identità del file CBOE originale: l'orario di acquisizione e il timestamp CBOE sono distinti.")
        unit = "ES equivalente" if e.get("es_basis") is not None else "SPX"
        shift = _number(e["es_basis"]) if e.get("es_basis") is not None else 0.0
        if e.get("es_basis") is None:
            st.warning("Differenziale ES non disponibile: livelli mostrati in SPX, non confrontare direttamente con ES.")
        labels = [("Call Wall", e.get("call_wall_spx")), ("Put Wall", e.get("put_wall_spx")), ("Gamma Flip", e.get("gamma_flip_spx"))]
        metrics = st.columns(5)
        for col, (title, val) in zip(metrics, labels):
            col.metric(title, f"{_number(val)+shift:,.2f}" if val is not None else "N/D")
        metrics[3].metric("Regime Edge", str(e.get("regime", "N/D")))
        metrics[4].metric("NET GEX (mld USD/1%)", f"{e['net_gex']/1e9:,.2f}")
        a, b = st.tabs(["Profilo GEX", "Curva ipotetica / Confronto"])
        with a:
            rows = sorted(e["rows"], key=lambda v: v["strike_spx"])
            chart = go.Figure()
            for key, name, color in (("call_gex", "Call GEX", "#22b573"), ("put_gex", "Put GEX", "#e64b4b")):
                chart.add_bar(y=[r["strike_spx"]+shift for r in rows], x=[r[key]/1e9 for r in rows],
                              orientation="h", name=name, marker_color=color)
            chart.add_scatter(y=[r["strike_spx"]+shift for r in rows], x=[r["net_gex"]/1e9 for r in rows],
                              name="NET GEX", line=dict(color="#f4a2b3"), mode="lines")
            for name, val, color in (("Call Wall", e.get("call_wall_spx"), "#22b573"), ("Put Wall", e.get("put_wall_spx"), "#e64b4b"), ("Gamma Flip", e.get("gamma_flip_spx"), "#e7ae4e")):
                if val is not None:
                    chart.add_hline(y=_number(val)+shift, line_color=color, line_dash="dash",
                                    annotation_text=f"{name}: {_number(val)+shift:,.2f}")
            chart.update_layout(height=590, barmode="relative", xaxis_title="Miliardi USD/1%", yaxis_title=f"Prezzo {unit}")
            st.plotly_chart(chart, use_container_width=True)
        with b:
            curve = e.get("curve", [])
            if curve:
                chart = go.Figure(go.Scatter(x=[_number(x["spot_spx"])+shift for x in curve], y=[_number(x["net_gex"])/1e9 for x in curve], mode="lines"))
                chart.add_hline(y=0, line_dash="dot")
                chart.update_layout(xaxis_title=f"Prezzo ipotetico {unit}", yaxis_title="NET GEX miliardi USD/1%")
                st.plotly_chart(chart, use_container_width=True)
            st.caption("Confronto informativo: i Wall Edge non equivalgono ai livelli C1/P1 Serafini. La curva è uno scenario a variabili fisse, non una previsione.")

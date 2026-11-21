"""Source-verified *derived* Alpaca GEX SPX read-only panel.

Do not merge with the historically Cboe-authenticated Edge panels.
The 0DTE and Weekly reports are linked by SHA256 to the SAME Serafini
Alpaca snapshot. Data are research-only until a verified quality gate
and an intraday comparison have been completed.
"""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go
from alpaca_gex_verify import verify_pair, GEX_NAME, SOURCE, ROME

@st.cache_data(ttl=75, max_entries=30, show_spinner=False)
def _report(week,name,_store):
    return verify_pair(_store,week,name)


def _fmt(value):
    return f"{float(value):,.2f}" if value is not None else "N/D"


def _curve_figure(report):
    curve=report["curve"]
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=[p["spot_spx"] for p in curve],
        y=[p["net_gex"]/1e9 for p in curve],mode="lines",
        line=dict(color="#00b5d8",width=2),name="GEX teorico"))
    fig.add_hline(y=0,line_color="#888888",line_width=1)
    spot=report["spot_spx"]
    fig.add_vline(x=spot,line_dash="dot",line_color="#eeeeee",
                  annotation_text="SPX (stima parità)")
    for value,color,title in (
        (report["gamma_flip_spx"],"#facc15","Gamma Flip"),
        (report["call_wall_spx"],"#22b573","Call Wall"),
        (report["put_wall_spx"],"#e65353","Put Wall")):
        if value is not None:
            fig.add_vline(x=float(value),line_color=color,line_dash="dash",
                          annotation_text=title,annotation_position="top")
    fig.update_layout(height=430,margin=dict(l=25,r=30,t=25,b=30),
                      xaxis_title="Prezzo SPX teorico",
                      yaxis_title="NET GEX (miliardi USD per 1%)",
                      showlegend=False)
    return fig


def _levels_figure(report):
    rows=report["rows"]
    fig=go.Figure()
    for field,label,color in (("call_gex","Call GEX","#22b573"),
                              ("put_gex","Put GEX","#e65353")):
        fig.add_trace(go.Bar(y=[r["strike_spx"] for r in rows],
                             x=[r[field]/1e9 for r in rows],
                             orientation="h",name=label,marker_color=color))
    fig.update_layout(height=490,barmode="relative",
                      margin=dict(l=25,r=30,t=20,b=30),
                      xaxis_title="Miliardi USD per movimento SPX dell'1%",
                      yaxis_title="Strike SPX",legend=dict(orientation="h"))
    return fig


def render(week,store):
    st.caption("GEX **calcolato dentro Serafini SPX**, senza app Edge esterna. "
               "Modello Edge-style: (Call + / Put −) × Gamma BSM × OI × 100 × SPX² × 0,01. "
               "Fonte Alpaca **indicative** + OI OCC, diversa dal NET GAMMA Serafini.")
    try:
        names=sorted([
            rec["name"] for rec in store.list(f"gex-alpaca/{week}/")
            if GEX_NAME.fullmatch(str(rec.get("name","")))
        ],reverse=True)
    except (RuntimeError,KeyError,TypeError,OSError) as exc:
        st.warning("Archivio GEX Alpaca non disponibile: "+type(exc).__name__)
        return
    if not names:
        st.info("Nessun GEX Alpaca archiviato per questa settimana. "
                "Le fotografie Cboe storiche restano consultabili nella sezione Edge originale.")
        return
    # Do not silently substitute a corrupted recent capture with yesterday's.
    choice=st.selectbox("Rilevamento GEX · provenienza Alpaca",
                        names,format_func=lambda name:name.replace("GEX_ALPACA_",""),
                        key=f"alpaca_gex_source_{week}")
    try:
        pair,clock=_report(week,choice,store)
    except (ValueError,KeyError,TypeError,RuntimeError,OSError,OverflowError) as exc:
        st.error("GEX N/D: associazione con la fotografia SPX non valida "
                 f"({type(exc).__name__}). Non vengono mostrati livelli alternativi.")
        return
    stamp=clock.astimezone(ROME)
    st.caption(f"Acquisizione {stamp:%d/%m/%Y %H:%M:%S} Roma · "
               f"{'ufficiale' if pair['kind']=='official' else 'Replay'} · "
               "modello derivato NON certificato Edge/Cboe.")
    chosen=st.radio("Scadenza GEX",("0DTE","Weekly"),horizontal=True,
                    key=f"alpaca_gex_expiry_{week}")
    report=pair["reports"][chosen]
    st.warning("QUALITÀ NON CERTIFICATA — valori solo diagnostici. "
               "Con feed indicative e Open Interest senza data OCC verificabile "
               "non considerare Wall, Flip o regime come segnali operativi.")
    columns=st.columns(4)
    for col,label,key in zip(columns,
                             ("Call Wall SPX","Put Wall SPX","Gamma Flip SPX","Regime GEX"),
                             ("call_wall_spx","put_wall_spx","gamma_flip_spx","regime")):
        col.metric(label, str(report[key]) if key=="regime" else _fmt(report.get(key)))
    a,b,c=st.columns(3)
    a.metric("NET GEX (mld USD / 1%)",f"{report['net_gex']/1e9:+.3f}")
    b.metric("Copertura contratti (diagnostica)",f"{report['quality_pct']:.2f}%")
    c.metric("Contratti modellati",int(report["contracts_valid"]))
    st.caption("Gamma Flip calcolato con sweep teorico ±10% (0,5 punti SPX). "
               "IV, OI e tempo a scadenza congelati al singolo rilevamento. "
               "La copertura percentuale NON è il quality gate Edge 1.3.")
    tab1,tab2=st.tabs(("GEX per strike","Curva teorica GEX"))
    with tab1:
        st.plotly_chart(_levels_figure(report),use_container_width=True)
    with tab2:
        st.plotly_chart(_curve_figure(report),use_container_width=True)

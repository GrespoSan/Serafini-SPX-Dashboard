"""Three aligned, read-only views: ES price / Serafini / Edge GEX."""
from __future__ import annotations
import hashlib
from datetime import date, datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from edge_context import read_report, _number
from net_gamma import read_snapshot, profile
from replay_prices import load_es_candles

def _matching_serafini(store, week, digest, day, reference):
    # Exact snapshot match: no comparing readings from different times.
    for item in store.list(f"{week}/snapshots/"):
        name = item.get("name", "")
        if not name.startswith("SPX_CBOE_" + day.strftime("%Y%m%d") + "_"):
            continue
        raw = store.download(f"{week}/snapshots/{name}")
        if hashlib.sha256(raw).hexdigest() != digest:
            continue
        result = profile(read_snapshot(raw), day, reference)
        return result["rows"], name
    return None, None

def _plot(e, serafini_rows, candles, shift, bounds):
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
        column_widths=[0.5,0.25,0.25], horizontal_spacing=0.038,
        subplot_titles=("PREZZO ES · 5 minuti", "SERAFINI · NET Gamma 0DTE", "EDGE · GEX 0DTE"))
    if candles is not None and not candles.empty:
        fig.add_trace(go.Candlestick(x=candles["bar_close"], open=candles["open"],
            high=candles["high"], low=candles["low"], close=candles["close"],
            name="ES=F Yahoo", increasing_line_color="#21b77c",
            decreasing_line_color="#e65353", showlegend=False),row=1,col=1)
    if serafini_rows:
        y = [float(r["strike_spx"])+shift for r in serafini_rows]
        n = [float(r["net_gamma"]) for r in serafini_rows]
        fig.add_trace(go.Bar(x=n,y=y,orientation="h",marker_color=[
            "#22b573" if x>=0 else "#e64b4b" for x in n],
            width=3.6,name="NET Serafini",showlegend=False),row=1,col=2)
        # Strike labels next to the central zero axis, matching Serafini Replay.
        # Use the existing original Serafini strike values, no new levels inferred.
        for strike in sorted(set(y)):
            if bounds[0] <= strike <= bounds[1]:
                fig.add_annotation(
                    x=0, y=strike, xref="x2", yref="y2",
                    text=f"{strike:,.0f}", showarrow=False,
                    xanchor="left", xshift=7,
                    font=dict(size=10, color="#d6dce5"),
                    bgcolor="rgba(12,17,26,0.50)",
                    borderpad=1,
                )
    rows=e["rows"]
    if rows:
        y=[_number(r["strike_spx"])+shift for r in rows]
        for key,name,color in (("call_gex","Call","#22b573"),("put_gex","Put","#e64b4b")):
            fig.add_trace(go.Bar(x=[_number(r[key])/1e9 for r in rows],y=y,orientation="h",
                marker_color=color,width=3.6,name=name,showlegend=False),row=1,col=3)
        fig.add_trace(go.Scatter(x=[_number(r["net_gex"])/1e9 for r in rows],
            y=y,mode="lines",line=dict(color="#f3a2b3",width=1.7),
            name="NET Edge",showlegend=False),row=1,col=3)
    levels=[("Call Wall",e.get("call_wall_spx"),"#22b573"),
            ("Put Wall",e.get("put_wall_spx"),"#e64b4b"),
            ("Gamma Flip",e.get("gamma_flip_spx"),"#dca143")]
    # Identical level price references across all three panels.
    for name,val,color in levels:
        if val is None:continue
        y=_number(val)+shift
        for col in (1,2,3):
            fig.add_hline(y=y,line_dash="dot",line_color=color,line_width=1,
                opacity=0.65,row=1,col=col)
    fig.update_yaxes(range=list(bounds),row=1,col=1,title="Prezzo ES equivalente")
    fig.update_yaxes(range=list(bounds),row=1,col=2)
    fig.update_yaxes(range=list(bounds),row=1,col=3)
    fig.update_layout(height=690,barmode="relative",
        margin=dict(l=40,r=20,t=65,b=50),showlegend=False)
    fig.update_xaxes(rangeslider_visible=False,row=1,col=1)
    fig.update_xaxes(title_text="NET Serafini",row=1,col=2)
    fig.update_xaxes(title_text="GEX mld USD/1%",row=1,col=3)
    return fig

def show(week, store):
    with st.expander("CONFRONTO — Prezzo | Serafini | Edge GEX",expanded=False):
        st.caption("Tre pannelli affiancati con scala ES condivisa; i motori restano indipendenti.")
        if st.button(
            "Aggiorna analisi · ultima fotografia",
            key="spx_refresh_both",
            help="Seleziona la fotografia CBOE più recente per i calcoli separati Serafini ed Edge. Non acquisisce nuovi dati CBOE.",
        ):
            st.session_state[f"spx_refresh_latest_{week}"] = True
            st.session_state.pop("edge_compare_yzoom", None)
            st.rerun()
        ctx=st.session_state.get(f"gamma_compare_context_{week}")
        source=st.radio("Origine GEX",("Automatico · stessa fotografia Serafini","Carica JSON Edge (alternativa)"),horizontal=True,key="edge_compare_source")
        if source.startswith("Automatico"):
            if not ctx or not ctx.get("snapshot"):
                st.info("Apri NET GAMMA 0DTE / Weekly e seleziona la fotografia da confrontare.")
                return
            if ctx.get("offset") is None:
                st.warning("Differenziale ES−SPX non disponibile per la fotografia selezionata.")
                return
            try:
                from edge_auto import result_from_snapshot
                raw=store.download(f"{week}/snapshots/{ctx['snapshot']}")
                e=result_from_snapshot(raw,float(ctx["offset"]))
                st.caption("GEX calcolato automaticamente con motore Edge indipendente, dalla stessa fotografia originale CBOE.")
            except Exception as err:
                st.error("Calcolo Edge automatico non disponibile: "+str(err))
                st.info("È ancora possibile caricare il report JSON Edge selezionando la modalità alternativa.")
                return
        else:
            file=st.file_uploader("Report Edge GEX (JSON)",type=["json"],key="edge_compare_json_3col")
            if file is None:
                st.info("Esporta «Confronto Edge per SPX (JSON)» dall'app Edge e caricalo qui.")
                return
            try:
                e=read_report(file.getvalue())
            except (ValueError,KeyError,TypeError) as err:
                st.error(str(err));return
        if not ctx:
            st.warning("Apri prima NET GAMMA 0DTE/Weekly e scegli la fotografia e il riferimento Serafini.")
            return
        try:
            day=ctx["day"]
            if isinstance(day,str):day=date.fromisoformat(day)
            if e["mode"]!="0DTE" or e["expiries"]!=[day.isoformat()]:
                st.warning("Edge non è 0DTE della stessa giornata Serafini: confronto sospeso.")
                return
            if ctx.get("offset") is None or e.get("es_basis") is None:
                st.warning("Manca il differenziale ES−SPX in uno dei due sistemi.")
                return
            shift=_number(e["es_basis"])
            if abs(shift-float(ctx["offset"]))>0.51:
                st.warning("Differenziali ES−SPX diversi: confronto sospeso.")
                return
            sr,name=_matching_serafini(store,week,e["sha256"],day,float(ctx["ref"]))
            if sr is None:
                st.warning("Fotografia CBOE Edge non trovata identica nell'archivio Serafini (SHA-256): nessun confronto Serafini disegnato.")
                return
        except Exception as err:
            st.error("Impossibile verificare la fotografia Serafini: "+str(err));return
        price=float(e["spot_spx"])+shift
        st.caption(f"Fotografia identica: {name} · SHA256 {e['sha256'][:12]} · Scadenza {day} · Basis ES−SPX {shift:+g}")
        p=st.columns(5)
        p[0].metric("Spot ES equivalente",f"{price:,.2f}")
        for c,key,lab in zip(p[1:4],("put_wall_spx","gamma_flip_spx","call_wall_spx"),("Put Wall","Gamma Flip","Call Wall")):
            v=e.get(key);c.metric(lab,f"{float(v)+shift:,.2f}" if v is not None else "N/D")
        p[4].metric("Regime Edge",str(e.get("regime","N/D")))
        try:
            candles=load_es_candles(day)
            if candles.empty:raise ValueError("Nessuna candela disponibile")
        except Exception as err:
            candles=None
            st.warning("Prezzi ES 5 minuti non disponibili; nessuna candela simulata. "+str(err))
        y_values=[price]+[float(r["strike_spx"])+shift for r in sr]
        y_values += [float(r["strike_spx"])+shift for r in e["rows"]]
        if candles is not None and not candles.empty:
            y_values += candles["low"].tolist()+candles["high"].tolist()
        all_bounds=(min(y_values)-10,max(y_values)+10)
        view=st.radio("Scala verticale condivisa",("Zoom ±100 punti","Vista completa"),horizontal=True,key="edge_compare_yzoom")
        bounds=(price-100,price+100) if view.startswith("Zoom") else all_bounds
        fig=_plot(e,sr,candles,shift,bounds)
        st.plotly_chart(fig,use_container_width=True,key="edge_compare_three_charts")
        st.markdown(
            "**Legenda GEX:** 🟢 **Call GEX** (barre verdi) · 🔴 **Put GEX** (barre rosse) · "
            "**NET GEX** (linea rosa) · "
            "**Call Wall** (tratteggio verde) · **Put Wall** (tratteggio rosso) · "
            "**Gamma Flip** (tratteggio giallo)."
        )
        st.caption("ES=F Yahoo può differire dal future specifico e dal prezzo ES equivalente. Le linee GEX sono riferimenti Edge, non segnali Serafini.")
        with st.expander("Curva teorica GEX",expanded=False):
            curve=e.get("curve",[])
            if curve:
                f=go.Figure(go.Scatter(x=[_number(r["spot_spx"])+shift for r in curve],y=[_number(r["net_gex"])/1e9 for r in curve],mode="lines"))
                f.add_hline(y=0,line_dash="dot")
                st.plotly_chart(f,use_container_width=True)

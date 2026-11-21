"""Source-verified *derived* Alpaca GEX SPX read-only panel.

Do not merge with the historically Cboe-authenticated Edge panels.
The 0DTE and Weekly reports are linked by SHA256 to the SAME Serafini
Alpaca snapshot. Data are research-only until a verified quality gate
and an intraday comparison have been completed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

ROME=ZoneInfo("Europe/Rome")
SOURCE="ALPACA_INDICATIVE_OCC_OI"
SCHEMA="serafini_alpaca_gex_pair_v1"
SNAP_NAME=re.compile(r"^SPX_ALPACA_\d{8}_\d{6}\.json\.gz$")
GEX_NAME=re.compile(r"^GEX_ALPACA_\d{8}_\d{6}_(official|replay)_[A-Za-z0-9_* -]+\.json$")


def _finite(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError,OverflowError):
        return None


def _aware(value):
    try:
        moment=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return moment if moment.tzinfo is not None else None
    except (ValueError,TypeError):
        return None


def _check_report(report,expiry):
    if not isinstance(report,dict):
        raise ValueError("GEX vuoto")
    if (report.get("schema")!="serafini_alpaca_gex_v1" or report.get("source")!=SOURCE
            or report.get("expiries")!=[expiry]):
        raise ValueError("Scadenza/provenienza GEX non corrispondente")
    for key in ("spot_spx","net_gex","gross_gex","call_gex","put_gex"):
        if _finite(report.get(key)) is None:
            raise ValueError(f"GEX numerico mancante: {key}")
    if not math.isclose(float(report["call_gex"])+float(report["put_gex"]),
                        float(report["net_gex"]),rel_tol=1e-8,abs_tol=.01):
        raise ValueError("Totale GEX diverso da Call+Put")
    if report.get("quality_sufficient") is not False:
        # The Alpaca indicative quality gate is not certified equivalent
        # to the upstream Edge v1.3 exposure/OI freshness gate.
        raise ValueError("Qualità GEX Alpaca non dichiarata diagnostica")
    rows=report.get("rows")
    curve=report.get("curve")
    if not isinstance(rows,list) or not 50<=len(rows)<=10000:
        raise ValueError("Profilo strike GEX insufficiente")
    if not isinstance(curve,list) or len(curve)!=201:
        raise ValueError("Curva scenario GEX non completa")
    last=None
    for point in curve:
        s=_finite(point.get("spot_spx")); y=_finite(point.get("net_gex"))
        if s is None or y is None or (last is not None and s<=last):
            raise ValueError("Curva GEX non ordinata/finita")
        last=s
    for row in rows:
        values=tuple(_finite(row.get(k)) for k in ("strike_spx","call_gex","put_gex","net_gex"))
        if any(v is None for v in values):
            raise ValueError("Valore strike GEX non numerico")
        if not math.isclose(values[1]+values[2],values[3],rel_tol=1e-8,abs_tol=.01):
            raise ValueError("GEX strike Call+Put incoerente")
    for key in ("call_wall_spx","put_wall_spx","gamma_flip_spx"):
        v=report.get(key)
        if v is not None and _finite(v) is None:
            raise ValueError("Livello GEX non valido")


def verify_pair(store, week, name):
    """Download and bind GEX pair to the exact official/Replay Serafini source."""
    if not isinstance(week,str) or not re.fullmatch(r"\d{4}-W\d{2}",week):
        raise ValueError("Settimana non valida")
    if not GEX_NAME.fullmatch(str(name)):
        raise ValueError("Nome del GEX Alpaca non ammesso")
    raw=store.download(f"gex-alpaca/{week}/{name}")
    if len(raw)>5_000_000:
        raise ValueError("Report GEX troppo grande")
    pair=json.loads(raw)
    if (not isinstance(pair,dict) or pair.get("schema")!=SCHEMA or
            pair.get("source")!=SOURCE or pair.get("symbol")!="SPX"):
        raise ValueError("Schema GEX Alpaca non verificato")
    stamp=_aware(pair.get("download_rome"))
    if stamp is None or pair.get("kind") not in ("official","replay"):
        raise ValueError("Data/download GEX mancante")
    digest=pair.get("source_snapshot_sha256")
    if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise ValueError("Hash sorgente GEX mancante")
    reports=pair.get("reports")
    if not isinstance(reports,dict) or set(reports)!= {"0DTE","Weekly"}:
        raise ValueError("Due scadenze GEX obbligatorie")
    for label,report in reports.items():
        dates=report.get("expiries") if isinstance(report,dict) else None
        if not isinstance(dates,list) or len(dates)!=1:
            raise ValueError("Scadenza GEX mancante")
        _check_report(report,dates[0])
        if report.get("source_snapshot_sha256")!=digest:
            raise ValueError("GEX generato da un'altra fotografia")
        if report.get("captured_utc")!=pair.get("captured_utc"):
            raise ValueError("Ora GEX incoerente")
    orig=pair.get("related_serafini_path")
    if not isinstance(orig,str):
        raise ValueError("Collegamento sorgente GEX mancante")
    if pair["kind"]=="official":
        expected=f"{week}/snapshots/{pair.get('source_snapshot_filename')}"
        if orig!=expected or not SNAP_NAME.fullmatch(str(pair.get("source_snapshot_filename"))):
            raise ValueError("File Serafini SPX/Alpaca non corrispondente")
        archived=store.download(orig)
        if hashlib.sha256(archived).hexdigest()!=digest:
            raise ValueError("Snapshot Serafini diverso dal GEX")
        snapshot=json.loads(gzip.decompress(archived))
        prov=snapshot.get("source_provenance") or {}
        if (prov.get("source")!=SOURCE or
                prov.get("kind")!="DERIVED_ALPACA_INDICATIVE_NOT_CBOE" or
                prov.get("download_utc")!=pair["captured_utc"]):
            raise ValueError("Provenienza della fotografia originale non verificata")
    else:
        if not re.fullmatch(rf"replay/{re.escape(week)}/\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}}\.json",orig):
            raise ValueError("Nome Replay sorgente non consentito")
        compact=json.loads(store.download(orig))
        meta=compact.get("source_provenance") or {}
        if (compact.get("source_snapshot_sha256")!=digest or
                compact.get("source")!=SOURCE or
                compact.get("kind")!="STUDIO_INTRADAY_ALPACA_DERIVED" or
                meta.get("kind")!="DERIVED_ALPACA_INDICATIVE_NOT_CBOE" or
                compact.get("download_rome")!=pair.get("download_rome")):
            raise ValueError("Replay e GEX non appartengono alla stessa acquisizione")
    return pair,stamp


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

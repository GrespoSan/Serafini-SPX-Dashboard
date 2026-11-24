"""Render GEX for the exact selected NET GAMMA photograph, never a different time."""
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import streamlit as st

from csv_gex_panel import render as render_csv, render_report
from csv_gex_services import _cached_gex, _cached_occ, OI_LABELS, DEFAULT_OI_LABEL
from csv_gex_cboe_import import csv_to_snapshot
from csv_gex_core.calendar import NY
from spx_csv_gex_adapter import analyze_alpaca_snapshot
from spx_snapshot_source import parse_name
from gamma_section import _selected_snapshot

@st.cache_data(ttl=86400, max_entries=12, show_spinner=False)
def _alpaca_report(doc, day):
    return analyze_alpaca_snapshot(doc, day)

def _prepare_csv(payload):
    symbol = "SPX"
    oi_mode = OI_LABELS.get(st.session_state.get("csv_gex_oi_source", DEFAULT_OI_LABEL), "auto")
    iv_mode = {
        "Rilevamento automatico (blocco se IV ambigua)": "auto",
        "IV decimale · 0,20 = 20%": "decimal",
        "IV percentuale · 20 = 20%": "percent",
    }.get(st.session_state.get("csv_gex_iv_units"), "auto")
    st.session_state["csv_gex_last_report"] = None
    st.session_state["csv_gex_last_error"] = None
    try:
        occ, provenance, error = None, None, None
        if oi_mode != "csv":
            try:
                doc = csv_to_snapshot(payload, symbol)
                stamp = datetime.strptime(doc["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if stamp.astimezone(NY).date() != datetime.now(NY).date():
                    raise ValueError("CSV storico: non associare OI OCC corrente.")
                accepted = tuple(sorted({str(x["option"]) for x in doc["data"]["options"]}))
                occ, provenance = _cached_occ(symbol, accepted)
            except Exception as exc:
                error = str(exc)[:230]
        report = _cached_gex(payload, symbol, iv_mode, oi_mode, occ, provenance, error)
        st.session_state["csv_gex_last_report"] = ((hashlib.sha256(payload).hexdigest(), symbol, iv_mode, oi_mode), report)
    except Exception as exc:
        st.session_state["csv_gex_last_error"] = type(exc).__name__ + ": " + str(exc)[:230]

def _rome(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y %H:%M:%S") + " Roma"
    except (ValueError, TypeError):
        return "N/D"

def render(week, store, uploaded=None):
    ctx = st.session_state.get(f"gamma_compare_context_{week}")
    if not ctx:
        st.info("Seleziona prima una fotografia NET GAMMA valida.")
        return
    basis = ctx["offset"]
    if uploaded is not None and uploaded.name.lower().endswith(".csv"):
        payload = uploaded.getvalue()
        _prepare_csv(payload)
        render_csv(payload, "SPX", es_spx_basis=basis)
        return
    choice = ctx["snapshot"]
    if parse_name(choice)[0] != "ALPACA":
        st.info("Per JSON Cboe storico resta disponibile il pannello storico GEX EDGE sottostante. Il nuovo motore usa CSV originale o snapshot Alpaca con IV documentata.")
        return
    snap = _selected_snapshot(week, choice, store)
    info = _alpaca_report(snap, ctx["day"])
    st.markdown("**ALPACA indicative + Open Interest archiviato · Gamma BSM modellato**")
    a,b = st.columns(2)
    a.markdown("**Ultima quotazione della fotografia**  \n"+_rome(info["timestamp_utc"])+"  \n**Download:** "+_rome(info["download_utc"]))
    b.markdown("**Open Interest effettivo nello snapshot**  \n"+", ".join(info["oi_sources"])+"  \n**Data OI:** "+str(info["oi_vintage"] or "N/D"))
    st.caption("Identità fotografia: "+choice+" · "+info["model"]+" · feed indicative, non OPRA. Nessun nuovo download di OI per ricalcolare una fotografia storica.")
    if info["quote_age_mode"] == "PREMARKET_PRIOR_SESSION":
        st.warning("Pre-sessione: quotazioni e IV riferite alla seduta precedente.")
    st.warning("GEX diagnostico: i contratti scartati dall'acquisitore non sono presenti nell'archivio. Il gate sul campione conservato non certifica la copertura dell'intera catena. Questi risultati non alimentano automaticamente il Pine o i segnali operativi.")
    for q in info["expiry_quality"]:
        st.caption(f"{q.get('expiry')}: {q.get('options_valid')} contratti conservati · scarti a monte {q.get('rejected')} · Gamma da IV opposta {q.get('gamma_recovered','N/D')}.")
    render_report(info, "SPX", es_spx_basis=basis)

"""Visualizzazione GEX EDGE Replay, separata dal calcolo Edge e da Serafini.

Scelta di due sidecar Edge reali, datati e verificati contro il medesimo
snapshot compatto Serafini. Nessuna ricostruzione GEX dal NET GAMMA.
"""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from edge_replay_data import list_edge_slots, read_pair, same_model
from edge_gex_exposure import format_gex_billions
from replay_prices import load_es_candles, candles_up_to
from replay_profiles import choose_rows
from spx_market_clock import classify_0dte_download

ROME = ZoneInfo("Europe/Rome")


@st.cache_data(ttl=90, show_spinner=False, max_entries=40)
def _cached_slots(week, day, _store):
    # Il parametro privato _store non diventa parte della chiave cache.
    # Una sessione Streamlit usa un solo backend Supabase configurato.
    return list_edge_slots(_store, week, day)


@st.cache_data(ttl=90, show_spinner=False, max_entries=80)
def _cached_pair(week, day, slot, _store):
    return read_pair(_store, week, day, slot)


def _basis(week, day):
    context = st.session_state.get(f"gamma_compare_context_{week}") or {}
    if str(context.get("day")) == day.isoformat():
        try:
            offset = float(context["offset"])
            if math.isfinite(offset):
                return offset
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    key = f"basis_override_{day.isoformat()}"
    value = str(st.session_state.get(key, "")).strip().replace(",", ".")
    try:
        offset = float(value)
        if math.isfinite(offset):
            return offset
    except (TypeError, ValueError):
        pass
    cached = st.session_state.get("auto_basis_results", {}).get(day.isoformat())
    if isinstance(cached, dict):
        try:
            offset = float(cached["basis"])
            if math.isfinite(offset):
                return offset
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return None


def _number(value):
    return float(value) if value is not None else None


def _metric_price(label, value, shift, prev=None):
    if value is None:
        st.metric(label, "N/D")
        return
    current = float(value) + shift
    delta = (f"{float(value)-float(prev):+.2f} pt"
             if prev is not None else None)
    st.metric(label, f"{current:,.2f}", delta=delta)


def show_replay(week, store, plotter):
    iso_year, iso_week = map(int, week.split("-W"))
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    weekdays = [monday + timedelta(days=i) for i in range(5)]
    today = datetime.now(ROME).date()
    default = today if today in weekdays else weekdays[0]
    day = st.selectbox(
        "Giornata Replay Edge", weekdays, index=weekdays.index(default),
        format_func=lambda value: value.strftime("%d/%m/%Y"),
        key=f"edge_replay_day_{week}",
    )
    if st.button("Aggiorna rilevamenti Edge",
                 key=f"edge_replay_refresh_{week}_{day}",
                 help="Aggiorna solo l'elenco e i report Edge della giornata."):
        _cached_slots.clear()
        _cached_pair.clear()
        st.rerun()
    try:
        slots = _cached_slots(week, day, store)
    except (RuntimeError, ValueError, TypeError) as exc:
        st.warning(f"Archivio Edge Replay non disponibile: {type(exc).__name__}.")
        return
    if not slots:
        st.info(
            "Nessun risultato GEX Edge da Replay disponibile per questa giornata. "
            "I Replay compatti storici non consentono il ricalcolo: i report "
            "Edge vengono salvati soltanto dalle nuove acquisizioni. "
            "Puoi selezionare «Download ufficiali» sopra."
        )
        return
    names = [slot for slot, _ in slots]
    current = st.selectbox("Rilevamento Replay da studiare", names, index=len(names)-1,
                           key=f"edge_replay_clock_{week}_{day}")
    index = names.index(current)
    earlier = names[:index]
    previous_default = earlier[-1] if earlier else "Nessuno"
    compare = st.selectbox(
        "Confronta con",
        ["Nessuno"] + earlier,
        index=(earlier.index(previous_default)+1 if earlier else 0),
        key=f"edge_replay_compare_{week}_{day}_{current}",
        help="Solo orari precedenti della stessa giornata e stesso modello Edge.",
    )
    try:
        compact, sidecar, edge, clock = _cached_pair(week, day, current, store)
    except (RuntimeError, ValueError, TypeError, KeyError, OverflowError) as exc:
        st.warning(f"Fotografia Edge {current} non verificabile: {exc}")
        return
    if classify_0dte_download(day, compact.get("download_rome"))["post_expiry"]:
        st.warning("La fotografia è successiva alla scadenza SPXW 0DTE: "
                   "il confronto 0DTE non è valido.")
        return
    shift = _basis(week, day)
    if shift is None:
        st.warning("Differenziale ES−SPX non disponibile per la data selezionata. "
                   "Apri «ANALISI NET GAMMA» e verifica il differenziale della "
                   "stessa giornata; non mostro coordinate ES non verificate.")
        return
    previous_sidecar = None
    previous_edge = None
    if compare != "Nessuno":
        try:
            _, previous_sidecar, previous_edge, previous_clock = _cached_pair(
                week, day, compare, store)
            if not same_model(sidecar, previous_sidecar):
                st.warning("Confronto numerico fra i due orari sospeso: versione "
                           "Edge, scadenza o qualità non coerenti.")
                previous_edge = None
        except (RuntimeError, ValueError, TypeError, KeyError, OverflowError) as exc:
            st.warning(f"Il rilevamento {compare} non è confrontabile: {exc}")
            previous_edge = None
    good = edge.get("quality_sufficient") is True
    st.caption(
        f"Replay {current} · download {clock:%H:%M:%S} Roma · "
        f"Cboe {compact['cboe_timestamp_original']} (fuso non dichiarato) · "
        f"SHA-256 {sidecar['cboe_sha256'][:12]} · motore Edge indipendente "
        f"{str(edge.get('model_version', 'N/D'))}"
    )
    if not good:
        st.warning(
            f"QUALITÀ EDGE INSUFFICIENTE ({float(edge['quality_pct']):.2f}%). "
            "I valori sono soltanto diagnostici; regime Gamma N/D. "
            "Nessun dato viene pubblicato in Trend Daily."
        )
    else:
        st.caption(
            f"Qualità Edge: {float(edge['quality_pct']):.2f}% · "
            "Segno, Call/Put e NET GEX modellati sulla catena Cboe completa."
        )
    cols = st.columns(5)
    with cols[0]:
        st.metric("Spot ES equivalente", f"{float(edge['spot_spx']) + shift:,.2f}")
    for column, title, field in zip(cols[1:4],
                                    ("Put Wall", "Gamma Flip", "Call Wall"),
                                    ("put_wall_spx", "gamma_flip_spx", "call_wall_spx")):
        with column:
            _metric_price(title, edge.get(field), shift,
                          previous_edge.get(field) if previous_edge else None)
    with cols[4]:
        # Riutilizza la formattazione semantica già adottata nel confronto
        # ufficiale: il colore interessa il VALORE, non solo l'eventuale delta.
        from edge_triptych import _colored_edge_metric, _format_edge_quality
        _colored_edge_metric(
            cols[4], "Regime Edge",
            str(edge.get("regime", "N/D")) if good else "N/D",
            edge.get("regime") if good else None,
        )
    metrics = st.columns(4)
    for col, title, key in zip(metrics[:3],
                               ("NET GEX", "Call GEX", "Put GEX"),
                               ("net_gex", "call_gex", "put_gex")):
        amount = float(edge[key])
        old = (float(previous_edge[key]) if previous_edge else None)
        with col:
            if key == "net_gex":
                # In Streamlit st.metric il valore resta bianco anche quando
                # e' negativo: colorare il VALORE usando il segno reale Edge,
                # mantenendo separata la variazione rispetto al Replay scelto.
                _colored_edge_metric(col, title,
                                     format_gex_billions(amount, signed=True),
                                     amount)
                if old is not None:
                    col.caption(f"Δ {(amount - old)/1e9:+.2f} mld")
            else:
                st.metric(title, format_gex_billions(amount, signed=True),
                          delta=(f"{(amount - old)/1e9:+.2f} mld"
                                 if old is not None else None))
    with metrics[3]:
        # Il segno modellato è già rappresentato dal Regime Edge:
        # mostra qui una misura indipendente sulla qualità dell'analisi.
        quality = edge.get("quality_pct")
        st.metric(
            "Qualità Edge",
            _format_edge_quality(quality),
        )
    if previous_edge is not None:
        st.caption(
            f"Δ rispetto al Replay {compare} · stesso modello e scadenza "
            "0DTE, qualità validata in entrambe le fotografie."
        )
    try:
        candles = load_es_candles(day, required_until=clock)
        candles = candles_up_to(candles, clock)
    except (ValueError, TypeError, RuntimeError, KeyError, OverflowError) as exc:
        candles = None
        st.caption(f"Candele ES non disponibili: {exc}. GEX e Serafini restano visibili.")
    serafini_rows = choose_rows(compact["profiles"]["0DTE"], extended=True)
    price = float(edge["spot_spx"]) + shift
    prices = [price] + [float(row["strike_spx"]) + shift for row in serafini_rows]
    prices += [float(row["strike_spx"]) + shift for row in edge["rows"]]
    if candles is not None and not candles.empty:
        prices += candles["high"].tolist() + candles["low"].tolist()
    mode = st.radio(
        "Scala verticale condivisa",
        ("Zoom ±100 punti", "Vista completa"),
        horizontal=True, key=f"edge_replay_zoom_{week}_{day}",
    )
    bounds = (price-100, price+100) if mode.startswith("Zoom") else (min(prices)-10, max(prices)+10)
    chart = plotter(edge, serafini_rows, candles, shift, bounds,
                    reading_clock=clock.strftime("%H:%M:%S"))
    st.plotly_chart(chart, width="stretch",
                    key=f"edge_replay_chart_{week}_{day}_{current}_{compare}")
    st.caption(
        "Confronto informativo su dati 0DTE: Edge usa posizioni dei dealer "
        "MODELLATE, non osservate. NET GAMMA Serafini ed Edge sono distinti."
    )
    with st.expander("Curva teorica GEX Edge", expanded=False):
        curve = edge.get("curve") or []
        if curve:
            fig = go.Figure(go.Scatter(
                x=[float(row["spot_spx"])+shift for row in curve],
                y=[float(row["net_gex"])/1e9 for row in curve],
                mode="lines", name=f"Replay {current}"))
            if previous_edge is not None and previous_edge.get("curve"):
                fig.add_trace(go.Scatter(
                    x=[float(row["spot_spx"])+shift for row in previous_edge["curve"]],
                    y=[float(row["net_gex"])/1e9 for row in previous_edge["curve"]],
                    mode="lines", name=f"Replay {compare}",
                    line=dict(dash="dash")))
            fig.add_hline(y=0, line_dash="dot", line_color="#e8ba55")
            fig.update_layout(xaxis_title="Prezzo future ES equivalente",
                              yaxis_title="NET GEX miliardi USD/1%",
                              height=420)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Curva teorica non presente nel report Edge.")

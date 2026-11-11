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
from gex_cash_provenance import describe_spx_trade
from gex_price_sensitivity_lab import show_price_lab
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


def _verified_es_last_candle(candles, clock, *, max_age_minutes=11):
    """L'ultima ES 5m CHIUSA, e sufficientemente recente allo snapshot.

    Non usa il prezzo Cboe del cash SPX, né barre future. Un feed fermo
    non deve mai apparire come quotazione attuale del future.
    """
    if candles is None or candles.empty or clock.tzinfo is None:
        return None
    if "bar_close" not in candles or "close" not in candles:
        return None
    try:
        valid = candles.loc[candles["bar_close"] <= clock].sort_values("bar_close")
        if valid.empty:
            return None
        last = valid.iloc[-1]
        when = last["bar_close"]
        price = float(last["close"])
        age = (clock - when).total_seconds() / 60
        if not math.isfinite(price) or price <= 0 or not (0 <= age <= max_age_minutes):
            return None
        return {
            "price": price,
            "clock": when.astimezone(ROME).strftime("%H:%M"),
            "source": str(candles.attrs.get("candle_source", "ES 5m")),
            "age_minutes": round(age, 2),
        }
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def _model_valuation_context(edge, clock):
    """La data di valutazione del modello Edge NON è quella del download."""
    value = edge.get("valuation")
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            return {"label": "N/D", "prior_cash_session": False}
        rome_when = when.astimezone(ROME)
        return {
            "label": rome_when.strftime("%d/%m/%Y %H:%M"),
            "prior_cash_session": rome_when.date() < clock.astimezone(ROME).date(),
        }
    except (TypeError, ValueError, OverflowError, AttributeError):
        return {"label": "N/D", "prior_cash_session": False}


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
    # Il Cash SPX Cboe resta fisso fuori RTH. Le candele future ES sono
    # una fonte separata, usata SOLO per la quotazione ES e lo zoom.
    # Caricarle prima dei KPI evita di spacciare SPX+offset per prezzo ES.
    try:
        candles = load_es_candles(day, required_until=clock)
        candles = candles_up_to(candles, clock)
    except (ValueError, TypeError, RuntimeError, KeyError, OverflowError) as exc:
        candles = None
        st.caption(f"Candele ES non disponibili: {exc}. GEX e Serafini restano visibili.")
    last_es = _verified_es_last_candle(candles, clock)
    model_clock = _model_valuation_context(edge, clock)
    cash_spot = float(edge["spot_spx"])
    equivalent = cash_spot + shift

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
            "Copertura del modello, NON freschezza del prezzo cash SPX. "
            "Segno, Call/Put e NET GEX modellati sulla catena Cboe completa."
        )
    cash_trade_note, cash_trade_status = describe_spx_trade(
        edge.get("spx_cash_last_trade_time"), clock)
    st.caption(cash_trade_note)
    if cash_trade_status == "anomalia":
        st.warning("Timestamp cash SPX successivo al download: "
                   "verificare i dati Cboe prima di interpretare il modello.")
    if model_clock["prior_cash_session"]:
        st.warning(
            "RIFERIMENTO SPX NON LIVE: il modello GEX utilizza una valutazione "
            f"del {model_clock['label']} Roma (seduta cash precedente). "
            "Gli strike Wall e il Gamma Flip sono modellati su questo "
            "riferimento, NON ricalcolati sul prezzo corrente del future ES."
        )
    else:
        st.caption(f"Valutazione modello GEX: {model_clock['label']} Roma.")

    cols = st.columns(5)
    with cols[0]:
        st.metric("ES future · ultima chiusura 5m",
                  f"{last_es['price']:,.2f}" if last_es is not None else "N/D")
        if last_es is not None:
            st.caption(f"Barra {last_es['clock']} Roma · {last_es['source']}")
        else:
            st.caption("ES 5m non verificabile entro il download. "
                       "Nessun prezzo future ricostruito.")
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
    st.caption(
        f"Base della struttura GEX: SPX Cboe {cash_spot:,.2f} "
        f"· conversione teorica ES {equivalent:,.2f} "
        f"(differenziale ES−SPX {shift:+.2f}). "
        "NON è una quotazione ES corrente. Put Wall, Gamma Flip e Call Wall "
        "sono strike SPX convertiti con lo stesso differenziale fisso."
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
    serafini_rows = choose_rows(compact["profiles"]["0DTE"], extended=True)
    # La visualizzazione segue il prezzo ES verificato; la geometria degli
    # strike GEX continua a usare SPX + base e i dati grezzi NON cambiano.
    price = last_es["price"] if last_es is not None else equivalent
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
    # Laboratorio indipendente: SOLO la curva Edge archiviata, nessun
    # cambiamento alle Wall/Flip della sezione ufficiale o a Serafini.
    show_price_lab(edge, shift, last_es,
                   key=f"edge_scenario_{week}_{day}_{current}")

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
            fig.update_layout(xaxis_title="Scenario SPX convertito in coordinate ES",
                              yaxis_title="NET GEX miliardi USD/1%",
                              height=420)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Curva teorica non presente nel report Edge.")

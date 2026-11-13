"""Replay sperimentale delle curve Edge REALMENTE archiviate o ricalcolate da Cboe.

Sezione separata dal Replay Serafini e da GEX EDGE - CONFRONTO.
Nessun dato sintetico: le curve Replay provengono da sidecar validati contro
SHA della stessa fotografia; le ufficiali dall'originale Cboe archiviato.
Le candele ES sono solo quelle chiuse entro il download del fotogramma.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from edge_replay_data import list_edge_slots, read_pair
from edge_replay_panel import _basis
from gex_price_sensitivity_lab import scenario_gex
from replay_prices import load_es_candles
from spx_market_clock import classify_0dte_download

ROME = ZoneInfo("Europe/Rome")


def _official_entries(week, day, store, logs):
    from gamma_section import _snapshot_log_index
    index = _snapshot_log_index(
        week, tuple(str(item["name"]) for item in logs or []), store
    )
    entries = []
    for path, (_, _, clock) in index.items():
        if not path.startswith(f"{week}/snapshots/SPX_CBOE_"):
            continue
        if not isinstance(clock, datetime) or clock.tzinfo is None:
            continue
        stamp = clock.astimezone(ROME)
        if stamp.date() != day or stamp > datetime.now(ROME) + timedelta(minutes=5):
            continue
        entries.append((stamp, path))
    return tuple(sorted(set(entries)))


@st.cache_data(ttl=600, show_spinner=False, max_entries=10)
def _load_frames(week, day, basis, official, _store):
    from edge_auto import result_from_snapshot
    from net_gamma import read_snapshot

    frames, rejected = [], []
    try:
        slots = list_edge_slots(_store, week, day)
    except (ValueError, RuntimeError, OSError, TypeError) as exc:
        slots = []
        rejected.append(f"Elenco Replay: {type(exc).__name__}")
    for slot, _ in slots:
        try:
            compact, sidecar, edge, clock = read_pair(_store, week, day, slot)
            if classify_0dte_download(day, compact["download_rome"])["post_expiry"]:
                raise ValueError("Snapshot oltre scadenza 0DTE")
            frames.append({
                "clock": clock, "origin": "REPLAY", "slot": slot,
                "edge": edge, "sha": sidecar["cboe_sha256"],
                "engine": sidecar.get("edge_engine_commit"),
                "timestamp": compact.get("cboe_timestamp_original"),
            })
        except (ValueError, RuntimeError, TypeError, KeyError, OSError, OverflowError) as exc:
            rejected.append(f"Replay {slot}: {type(exc).__name__}")
    for clock, path in official:
        try:
            raw = _store.download(path)
            digest = hashlib.sha256(raw).hexdigest()
            source = read_snapshot(raw)
            if classify_0dte_download(day, clock.isoformat())["post_expiry"]:
                raise ValueError("Snapshot oltre scadenza 0DTE")
            edge = result_from_snapshot(raw, basis)
            if (edge.get("sha256") != digest or edge.get("mode") != "0DTE"
                    or edge.get("symbol") != "SPX"
                    or edge.get("expiries") != [day.isoformat()]):
                raise ValueError("Catena ufficiale o scadenza non coerente")
            frames.append({
                "clock": clock, "origin": "UFFICIALE", "slot": clock.strftime("%H:%M:%S"),
                "edge": edge, "sha": digest, "engine": edge.get("model_version"),
                "timestamp": source.get("timestamp"),
            })
        except (ValueError, RuntimeError, TypeError, KeyError, OSError,
                OverflowError, ImportError) as exc:
            rejected.append(f"Ufficiale {clock:%H:%M}: {type(exc).__name__}")
    frames.sort(key=lambda f: (f["clock"], f["origin"] != "UFFICIALE"))
    return frames, rejected


def _curve_points(edge, basis):
    pairs = sorted((float(r["spot_spx"]) + basis, float(r["net_gex"]) / 1e9)
                   for r in edge.get("curve") or [])
    if (len(pairs) < 2
            or not all(math.isfinite(x) and math.isfinite(y) for x, y in pairs)
            or any(x >= xx for (x, _), (xx, _) in zip(pairs, pairs[1:]))):
        raise ValueError("Curva GEX senza punti ordinati e finiti")
    return pairs


def _last_price(candles, clock):
    if candles is None or candles.empty:
        return None
    available = candles.loc[candles["bar_close"] <= clock]
    if available.empty:
        return None
    bar = available.iloc[-1]
    age = (clock - bar["bar_close"]).total_seconds() / 60
    price = float(bar["close"])
    if not (0 <= age <= 11 and math.isfinite(price) and price > 0):
        return None
    return price


def _traces(frame, candles, basis):
    edge = frame["edge"]
    points = frame["points"]
    prices = [x for x, _ in points]
    exposure = [y for _, y in points]
    maximum = max(points, key=lambda p: p[1])
    minimum = min(points, key=lambda p: p[1])
    cash_es = float(edge["spot_spx"]) + basis
    cash_gex = scenario_gex(edge["curve"], float(edge["spot_spx"]))
    clock = frame["clock"]
    history = (candles.loc[candles["bar_close"] <= clock]
               if candles is not None else None)
    observed = _last_price(candles, clock)
    actual_gamma = (scenario_gex(edge["curve"], observed - basis)
                    if observed is not None else None)
    flip = edge.get("gamma_flip_spx")
    flip_es = float(flip) + basis if flip is not None else None
    base = [
        go.Scatter(x=prices, y=exposure, mode="lines", name="Curva GEX Edge",
                   line=dict(color="#ae78eb", width=3)),
        go.Scatter(x=[cash_es] if cash_gex is not None else [],
                   y=[cash_gex / 1e9] if cash_gex is not None else [],
                   mode="markers", name="SPX Cboe + base (non ES live)",
                   marker=dict(color="#999999", size=11)),
        go.Scatter(x=[observed] if actual_gamma is not None else [],
                   y=[actual_gamma / 1e9] if actual_gamma is not None else [],
                   mode="markers", name="Ultimo ES 5m chiuso, sulla curva",
                   marker=dict(color="#42bfe5", size=12)),
        go.Scatter(x=[maximum[0], minimum[0]],
                   y=[maximum[1], minimum[1]], mode="markers+text",
                   name="Massimo / minimo curva", text=["MAX", "MIN"],
                   textposition="top center",
                   marker=dict(color=["#29ad7d", "#ec6868"], size=10)),
        go.Scatter(x=[flip_es] if flip_es is not None else [],
                   y=[0] if flip_es is not None else [],
                   mode="markers", name="Gamma Flip modellato",
                   marker=dict(symbol="diamond", color="#e6b84e", size=11)),
        go.Scatter(x=history["bar_close"].tolist() if history is not None else [],
                   y=history["close"].tolist() if history is not None else [],
                   mode="lines", name="ES · close 5m fino al download",
                   line=dict(color="#4cbbe5", width=2)),
        go.Scatter(x=[history.iloc[-1]["bar_close"]]
                   if observed is not None and history is not None else [],
                   y=[observed] if observed is not None else [],
                   mode="markers", name="ES disponibile al rilevamento",
                   marker=dict(color="#f4c95d", size=10)),
    ]
    previous = (float(history["close"].min()), float(history["close"].max())
                ) if history is not None and not history.empty else None
    if previous:
        spread = max(5., (previous[1] - previous[0]) * .1)
        price_range = [previous[0] - spread, previous[1] + spread]
    else:
        price_range = None
    return base, observed, price_range


def render(store, week, logs):
    st.subheader("Replay · Curva teorica GEX Edge e prezzo ES")
    st.caption(
        "Modulo sperimentale indipendente. Ogni fotogramma usa una curva "
        "Edge del proprio download Cboe, NON lo spostamento del prezzo "
        "su una curva fissa. Nessuna modifica ai modelli esistenti."
    )
    year, number = map(int, week.split("-W"))
    monday = date.fromisocalendar(year, number, 1)
    days = [monday + timedelta(days=i) for i in range(5)]
    today = datetime.now(ROME).date()
    day = st.selectbox("Giornata GEX 0DTE", days,
                       index=days.index(today) if today in days else 0,
                       format_func=lambda d: d.strftime("%d/%m/%Y"),
                       key=f"edge_curve_replay_day_{week}")
    basis = _basis(week, day)
    if basis is None:
        st.warning("Differenziale ES−SPX della giornata non disponibile. "
                   "Verificalo in ANALISI NET GAMMA: non invento coordinate ES.")
        return
    st.caption(f"Differenziale ES−SPX già verificato: {basis:+.2f} pt, "
               "fisso per l'intera visualizzazione.")
    if st.button("Aggiorna archivio del Replay GEX",
                 key=f"edge_curve_reload_{week}_{day}"):
        _load_frames.clear()
        st.rerun()
    if not st.toggle("Carica Replay GEX storico (download dall'archivio)",
                     value=False, key=f"edge_curve_enable_{week}_{day}"):
        st.info("Attiva il caricamento per visualizzare Play/Pausa e le curve archiviate. "
                "Sono utilizzati sia Replay validati sia download ufficiali; "
                "nessuna nuova acquisizione Cboe.")
        return
    try:
        official = _official_entries(week, day, store, logs)
        with st.spinner("Verifica curve Edge archiviate e catene ufficiali..."):
            frames, excluded = _load_frames(week, day, basis, official, store)
    except (ValueError, RuntimeError, TypeError, KeyError, OSError) as exc:
        st.warning(f"Archivio Replay GEX non accessibile: {type(exc).__name__}.")
        return

    good, omitted = [], list(excluded)
    for f in frames:
        try:
            if f["edge"].get("mode") != "0DTE" or f["edge"].get("expiries") != [day.isoformat()]:
                raise ValueError("Scadenza 0DTE errata")
            f["points"] = _curve_points(f["edge"], basis)
            good.append(f)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            omitted.append(f"{f['origin']} {f['slot']}: {type(exc).__name__}")
    if not good:
        st.warning("Nessuna curva Edge 0DTE verificabile per la data scelta.")
        if omitted:
            st.caption("Esclusioni: " + "; ".join(omitted[:10]))
        return
    versions = {str(f["edge"].get("model_version")) for f in good}
    if len(versions) > 1:
        st.warning("Versioni del modello Edge differenti nei fotogrammi: "
                   "non interpretare i cambiamenti di ampiezza come omogenei.")
    try:
        es = load_es_candles(day, required_until=good[-1]["clock"])
        es_source = es.attrs.get("candle_source", "ES 5m")
    except (ValueError, RuntimeError, TypeError, KeyError, OSError, OverflowError) as exc:
        es, es_source = None, "N/D"
        st.warning(f"ES 5 minuti non disponibile ({type(exc).__name__}); curve GEX visibili.")
    figure = make_subplots(rows=2, cols=1, shared_xaxes=False,
                           row_heights=[.64, .36], vertical_spacing=.13,
                           subplot_titles=("Curva GEX per prezzo ES teorico",
                                           "ES effettivo · sole barre chiuse"))
    first, _, first_range = _traces(good[0], es, basis)
    for i, trace in enumerate(first):
        figure.add_trace(trace, row=1 if i < 5 else 2, col=1)
    x_values = [x for f in good for x, _ in f["points"]]
    y_values = [y for f in good for _, y in f["points"]]
    x_margin = max(10, (max(x_values)-min(x_values)) * .02)
    y_margin = max(.1, (max(y_values)-min(y_values)) * .08)
    frames_plotly = []
    labels = []
    for i, f in enumerate(good):
        series, _, prices_range = _traces(f, es, basis)
        label = f"{f['clock']:%H:%M:%S} · {f['origin']}"
        labels.append(label)
        layout = go.Layout(title=f"GEX 0DTE · {day:%d/%m/%Y} · {label}")
        # L'asse temporale avanza col fotogramma: nessun prezzo futuro.
        layout.xaxis2 = dict(range=[
            datetime.combine(day, datetime.min.time(), tzinfo=ROME),
            f["clock"] + timedelta(minutes=5),
        ])
        if prices_range:
            layout.yaxis2 = dict(range=prices_range)
        frames_plotly.append(go.Frame(
            name=str(i), data=series, traces=list(range(7)), layout=layout))
    figure.frames = frames_plotly
    slider_steps = [dict(label=label, method="animate",
                         args=[[str(i)], {"mode": "immediate",
                                          "frame": {"duration": 0, "redraw": True},
                                          "transition": {"duration": 0}}])
                    for i, label in enumerate(labels)]
    figure.update_layout(
        title=f"GEX 0DTE · {day:%d/%m/%Y} · {labels[0]}",
        height=780, hovermode="closest",
        xaxis=dict(title="Prezzo ES teorico (SPX + base fissa)",
                   range=[min(x_values)-x_margin, max(x_values)+x_margin]),
        yaxis=dict(title="NET GEX · mld USD / 1%",
                   range=[min(y_values)-y_margin, max(y_values)+y_margin]),
        xaxis2=dict(title="Orario Roma · close ES 5 minuti", tickformat="%H:%M",
                    range=[datetime.combine(day, datetime.min.time(), tzinfo=ROME),
                           good[0]["clock"] + timedelta(minutes=5)]),
        yaxis2=dict(title="ES future", range=first_range) if first_range else dict(title="ES future"),
        legend=dict(orientation="h", y=1.13),
        updatemenus=[dict(type="buttons", direction="left",
                          x=0, y=-.08, xanchor="left",
                          buttons=[
                              dict(label="▶ Play", method="animate",
                                   args=[None, {"fromcurrent": True,
                                                "frame": {"duration": 900, "redraw": True},
                                                "transition": {"duration": 0}}]),
                              dict(label="⏸ Pausa", method="animate",
                                   args=[[None], {"mode": "immediate",
                                                  "frame": {"duration": 0, "redraw": False},
                                                  "transition": {"duration": 0}}])
                          ])],
        sliders=[dict(active=0, x=.16, len=.84, y=-.08,
                      currentvalue=dict(prefix="Rilevamento: "),
                      steps=slider_steps)]
    )
    figure.add_hline(y=0, line_dash="dash", line_color="#8a8a8a", row=1, col=1)
    st.plotly_chart(figure, width="stretch",
                    key=f"edge_curve_animation_{week}_{day}")
    table = []
    for f in good:
        curve = f["points"]
        maximum = max(curve, key=lambda p:p[1])
        minimum = min(curve, key=lambda p:p[1])
        flip = f["edge"].get("gamma_flip_spx")
        table.append({
            "Download Roma": f["clock"].strftime("%H:%M:%S"),
            "Fonte": f["origin"], "Slot": f["slot"],
            "Qualità Edge": ("VALIDA" if f["edge"].get("quality_sufficient") is True
                             else "SOLO DIAGNOSTICA"),
            "NET GEX originale (mld)": round(float(f["edge"]["net_gex"])/1e9, 3),
            "ES al massimo curva": round(maximum[0], 2),
            "GEX massimo (mld)": round(maximum[1], 3),
            "ES al minimo curva": round(minimum[0], 2),
            "GEX minimo (mld)": round(minimum[1], 3),
            "Gamma Flip ES": round(float(flip)+basis, 2) if flip is not None else None,
            "ES 5m verificato": _last_price(es, f["clock"]),
            "Versione modello": f["edge"].get("model_version"),
            "SHA Cboe": f["sha"][:12],
            "Timestamp Cboe originale": f["timestamp"],
        })
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(f"{len(good)} curve disponibili · fonte prezzo ES: {es_source}. "
               "La linea ES mostra soltanto chiusure 5m precedenti al download; "
               "il punto azzurro sulla curva è una INTERPOLAZIONE, non un nuovo "
               "calcolo GEX. Massimi/minimi sono estremi campionati della curva. "
               "La qualità insufficiente indica un risultato diagnostico.")
    repeated = len(good) - len({f["sha"] for f in good})
    if repeated:
        st.warning(f"{repeated} fotogrammi condividono una catena Cboe già "
                   "presente in giornata: non considerarli nuove osservazioni "
                   "indipendenti.")
    st.caption("Gli assi della curva mantengono una scala comune calcolata sulle "
               "fotografie archiviate della giornata: utile per la comparazione "
               "VISIVA, non utilizzabile come informazione disponibile ex ante.")
    if omitted:
        st.caption(f"Rilevamenti esclusi ({len(omitted)}): " + "; ".join(omitted[:12]))

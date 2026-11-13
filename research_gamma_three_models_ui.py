"""Laboratorio read-only: tre misure DIVERSE dalla stessa catena ufficiale Cboe.

Il NET GAMMA Serafini originale e il motore GEX Edge non vengono modificati.
OI-only usa esattamente gli stessi 50 strike di Serafini ma pesa solo OI.
Il GEX modellato Edge ha unità monetaria / 1% e un universo differente:
non confrontare numeri grezzi tra le tre scale.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from edge_curve_replay import _official_entries
from edge_replay_panel import _basis
from edge_gex_exposure import format_gex_billions
from net_gamma import parse_contract, profile, read_snapshot


def oi_only_rows(snapshot, expiry, serafini_rows):
    """Nessun prezzo sintetico e nessun gamma medio: usa le opzioni originali.

    La selezione dei 50 strike è quella GIA' prodotta da profile Serafini.
    Esegue un controllo indipendente di riconciliazione con la formula originale.
    """
    expected = {float(r["strike_spx"]): r for r in serafini_rows}
    if len(expected) != 50:
        raise ValueError("Servono i 50 strike originali di Serafini")
    sums = defaultdict(lambda: [0., 0., 0., 0.])
    for option in snapshot["data"]["options"]:
        parsed = parse_contract(str(option.get("option", "")))
        if parsed is None or parsed[0] != expiry:
            continue
        _, side, strike = parsed
        if strike not in expected:
            continue
        gamma = float(option.get("gamma") or 0)
        oi = float(option.get("open_interest") or 0)
        volume = float(option.get("volume") or 0)
        if not all(math.isfinite(v) and v >= 0 for v in (gamma, oi, volume)):
            raise ValueError("Gamma, OI o volume non validi nella catena Cboe")
        which = 0 if side == "C" else 1
        sums[strike][which] += oi * gamma
        sums[strike][which + 2] += (oi + volume) * gamma
    rows = []
    for strike in sorted(expected):
        call_oi, put_oi, call_full, put_full = sums[strike]
        original = float(expected[strike]["net_gamma"])
        if not math.isclose(call_full - put_full, original,
                            rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"Serafini non riconciliato allo strike {strike}")
        rows.append({
            "Strike SPX": strike,
            "NET GAMMA Serafini": original,
            "NET GAMMA solo OI": call_oi - put_oi,
            "Call gamma solo OI": call_oi,
            "Put gamma solo OI": put_oi,
        })
    return rows


@st.cache_data(ttl=600, show_spinner=False, max_entries=14)
def _snapshot_analysis(week, day, path, basis, _store):
    from edge_auto import result_from_snapshot
    raw = _store.download(path)
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = read_snapshot(raw)
    reference = float(snapshot["data"]["prev_day_close"])
    spot = float(snapshot["data"]["current_price"])
    if not all(math.isfinite(x) and x > 0 for x in (reference, spot)):
        raise ValueError("SPX chiusura precedente o spot non validi")
    serafini = profile(snapshot, day, reference)
    core = oi_only_rows(snapshot, day, serafini["rows"])
    edge = result_from_snapshot(raw, basis)
    if (edge.get("sha256") != digest or edge.get("symbol") != "SPX"
            or edge.get("mode") != "0DTE"
            or edge.get("expiries") != [day.isoformat()]):
        raise ValueError("Report GEX non riferito alla stessa catena Cboe")
    edge_map = {}
    for item in edge.get("rows") or []:
        key = float(item["strike_spx"])
        value = float(item["net_gex"])
        if not math.isfinite(key) or not math.isfinite(value) or key in edge_map:
            raise ValueError("Strike o NET GEX Edge duplicato/non finito")
        edge_map[key] = value
    for row in core:
        row["GEX Edge per strike (mld / 1%)"] = (
            edge_map[row["Strike SPX"]] / 1e9 if row["Strike SPX"] in edge_map
            else None
        )
    return {
        "path": path,
        "sha256": digest,
        "spot": spot, "reference": reference,
        "rows": core,
        "serafini_sum50": math.fsum(r["NET GAMMA Serafini"] for r in core),
        "oi_sum50": math.fsum(r["NET GAMMA solo OI"] for r in core),
        "edge_full": float(edge["net_gex"]),
        "edge_match": sum(r["GEX Edge per strike (mld / 1%)"] is not None
                          for r in core),
        "quality": edge.get("quality_sufficient") is True,
        "quality_pct": edge.get("quality_pct"),
        "model": edge.get("model_version"),
    }


def _one_snapshot_view(result):
    a, b, c = st.columns(3)
    a.metric("Serafini · 50 strike (OI + Volume)",
             f"{result['serafini_sum50']:+,.3f}")
    b.metric("NET GAMMA · stessi 50, solo OI",
             f"{result['oi_sum50']:+,.3f}")
    c.metric("NET GEX Edge · intera catena 0DTE",
             format_gex_billions(result["edge_full"], signed=True))
    st.caption(
        "I primi due sono nelle unità NET GAMMA di Serafini (50 strike). "
        "Il terzo è esposizione monetaria modellata in USD per movimento "
        "SPX dell'1%, sull'intera catena 0DTE Edge: NON sono valori "
        "confrontabili direttamente. Segno Edge = ipotesi dealer, non osservazione."
    )
    if not result["quality"]:
        st.warning("Qualità Edge insufficiente: curve e risultati Edge solo diagnostici.")
    if result["edge_match"] != 50:
        st.warning(f"GEX Edge disponibile solo per {result['edge_match']}/50 strike: "
                   "i mancanti restano N/D, NON assumono valore zero.")
    rows = result["rows"]
    figure = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[.33, .33, .34], vertical_spacing=.08,
        subplot_titles=("NET GAMMA Serafini · OI+volume (50 strike)",
                        "NET GAMMA solo OI (stessi 50 strike)",
                        "GEX Edge modellato · strike in comune (mld USD/1%)"))
    colors = ("#598ee2", "#e4b662", "#ba87dd")
    for i, key in enumerate(("NET GAMMA Serafini", "NET GAMMA solo OI",
                              "GEX Edge per strike (mld / 1%)"), 1):
        figure.add_trace(go.Bar(
            x=[r["Strike SPX"] for r in rows],
            y=[r[key] for r in rows],
            name=key, marker_color=colors[i-1]), row=i, col=1)
        figure.add_hline(y=0, row=i, col=1, line_color="#7e7e7e",
                         line_dash="dash", line_width=1)
    figure.update_layout(height=900, showlegend=False, bargap=.18)
    figure.update_xaxes(title="Strike SPX · identica selezione Serafini", row=3, col=1)
    st.plotly_chart(figure, width="stretch",
                    key="research_three_gamma_strike_comparison")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("I 50 strike Serafini sono scelti 25 sotto + 25 sopra il close "
               "precedente; i valori Edge per strike sono mostrati SOLO dove "
               "disponibili sulla stessa fotografia. Fonte Cboe SHA-256: "
               + result["sha256"][:16] + "…")


def _daily_view(measurements):
    if len(measurements) < 2:
        st.info("Per l'evoluzione servono almeno due fotografie ufficiali valide.")
        return
    good = [(clock, r) for clock, r in measurements if r["quality"]]
    if len(good) < 2:
        st.warning("Meno di due rilevamenti Edge con qualità sufficiente. "
                   "Evoluzione comparata sospesa.")
        return
    models = {r["model"] for _, r in good}
    if len(models) != 1:
        st.warning("Versioni Edge differenti: confronto temporale non omogeneo.")
        return
    strike_sets = [{r["Strike SPX"] for r in item["rows"]} for _, item in good]
    if len({frozenset(s) for s in strike_sets}) != 1:
        st.warning("Universo 50 strike Serafini diverso tra rilevamenti. "
                   "Non concateno variazioni su universi differenti.")
        return
    # Percent change from first, expressed relative to |first| for negative
    # baselines. Baseline zero -> N/D; no zero imputation.
    fields = (
        ("Serafini", "serafini_sum50"),
        ("Solo OI", "oi_sum50"),
        ("Edge GEX", "edge_full"),
    )
    base = good[0][1]
    times = [clock for clock, _ in good]
    graph = go.Figure()
    for label, key in fields:
        initial = base[key]
        changes = [
            (100. * (r[key] - initial) / abs(initial)) if initial != 0 else None
            for _, r in good
        ]
        graph.add_trace(go.Scatter(x=times, y=changes,
                                   mode="lines+markers", name=label))
    graph.add_hline(y=0, line_dash="dash", line_color="#777")
    graph.update_layout(
        title="Variazioni rispetto al primo download ufficiale valido",
        xaxis_title="Ora del download · Roma",
        yaxis_title="Δ % su |valore iniziale| · scale normalizzate",
        height=420, hovermode="x unified",
        xaxis=dict(tickformat="%H:%M"))
    st.plotly_chart(graph, width="stretch")
    st.caption(
        "Confronto descrittivo, NON test predittivo: per valori iniziali "
        "negativi la differenza è divisa per il MODULO della base; "
        "una base zero è N/D. Gamma Serafini e solo OI utilizzano "
        "gli stessi 50 strike. Edge è su tutta la catena 0DTE: "
        "l'ampiezza percentuale NON misura automaticamente un edge di trading."
    )
    st.dataframe([{
        "Download Roma": clock.strftime("%H:%M:%S"),
        "Serafini · 50": r["serafini_sum50"],
        "Solo OI · 50": r["oi_sum50"],
        "Edge · mld USD/1%": r["edge_full"] / 1e9,
        "Qualità Edge": "VALIDA" if r["quality"] else "DIAGNOSTICA",
        "Versione": r["model"],
        "SHA Cboe": r["sha256"][:12],
    } for clock, r in measurements], hide_index=True, width="stretch")


def render(week, store, logs):
    st.caption(
        "Confronto di ricerca su catene Cboe UFFICIALI originali, senza "
        "modificare Serafini o il modello Edge. I Replay compatti non "
        "contengono tutti i contratti necessari per ricalcolare solo OI."
    )
    year, number = map(int, week.split("-W"))
    monday = date.fromisocalendar(year, number, 1)
    days = [monday + timedelta(days=i) for i in range(5)]
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    day = st.selectbox("Giornata Cboe ufficiale", days,
                       index=days.index(today) if today in days else 0,
                       format_func=lambda d: d.strftime("%d/%m/%Y"),
                       key=f"gamma_three_models_day_{week}")
    basis = _basis(week, day)
    if basis is None:
        st.warning("Differenziale ES−SPX non verificato per questa data: "
                   "calcolo sospeso, senza valori ricostruiti.")
        return
    try:
        entries = _official_entries(week, day, store, logs)
    except (ValueError, RuntimeError, TypeError, KeyError, OSError) as exc:
        st.warning(f"Fotografie ufficiali non disponibili ({type(exc).__name__}).")
        return
    if not entries:
        st.info("Nessuna fotografia Cboe ufficiale verificata in questa giornata.")
        return
    choices = {f"{clock:%H:%M:%S} Roma · {path.rsplit('/', 1)[-1]}": (clock, path)
               for clock, path in entries}
    selected = st.selectbox(
        "Fotografia originale da confrontare", list(choices),
        index=len(choices) - 1, key=f"gamma_three_models_file_{week}_{day}")
    clock, path = choices[selected]
    state_key = f"gamma_three_models_active_{week}"
    if st.button("Calcola confronto sulle tre misure",
                 key=f"gamma_three_models_go_{week}_{day}"):
        st.session_state[state_key] = (day.isoformat(), path, float(basis))
    if st.session_state.get(state_key) != (day.isoformat(), path, float(basis)):
        st.caption("Premi il pulsante per eseguire i calcoli di questa fotografia.")
        return
    try:
        with st.spinner("Analisi dei contratti originali Cboe e modello Edge..."):
            result = _snapshot_analysis(week, day, path, basis, store)
        _one_snapshot_view(result)
    except (ValueError, RuntimeError, TypeError, KeyError, OSError,
            OverflowError, ImportError) as exc:
        st.warning(f"Confronto non disponibile: {type(exc).__name__}: {str(exc)[:140]}")
        return

    st.divider()
    st.markdown("**Evoluzione delle tre misure nella giornata (solo ufficiali)**")
    day_key = f"gamma_three_models_daily_{week}_{day}"
    if st.button("Calcola tutte le fotografie ufficiali del giorno",
                 key=f"gamma_three_models_all_{week}_{day}"):
        st.session_state[day_key] = tuple(entries)
    if st.session_state.get(day_key) == tuple(entries):
        measurements, missing = [], []
        for stamp, filename in entries:
            try:
                data = _snapshot_analysis(week, day, filename, basis, store)
                measurements.append((stamp, data))
            except (ValueError, RuntimeError, TypeError, KeyError, OSError,
                    OverflowError, ImportError) as exc:
                missing.append(f"{stamp:%H:%M}: {type(exc).__name__}")
        if measurements:
            _daily_view(measurements)
        if missing:
            st.caption("Fotografie non utilizzabili: " + "; ".join(missing))

"""Three aligned, read-only views: ES price / Serafini / Edge GEX."""
from __future__ import annotations
import math
import hashlib
from html import escape
from datetime import date, datetime
from zoneinfo import ZoneInfo
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from edge_context import read_report, _number
from net_gamma import read_snapshot, profile
from replay_prices import load_es_candles
from edge_gex_exposure import format_gex_billions
from gex_cash_provenance import extract_spx_trade_time, describe_spx_trade


def _edge_metric_color(value):
    """Semantic color for modeled Edge values; zero/unknown stay neutral."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return "inherit"
        return "#22b573" if value > 0 else "#e64b4b" if value < 0 else "inherit"
    sign = str(value or "").strip().lower()
    if sign in ("positive", "positivo"):
        return "#22b573"
    if sign in ("negative", "negativo"):
        return "#e64b4b"
    return "inherit"


def _colored_edge_metric(column, label, value, sign):
    """Color the metric VALUE (not just a delta), safely and without global CSS."""
    color = _edge_metric_color(sign)
    column.markdown(
        '<div style="min-height:5rem">'
        f'<div style="font-size:.875rem;line-height:1.5rem;opacity:.85">{escape(str(label))}</div>'
        f'<div style="font-size:2rem;line-height:2.45rem;font-weight:400;'
        f'color:{color};overflow-wrap:anywhere">{escape(str(value))}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _format_edge_quality(value):
    """Show real 0-100% Edge completeness, or N/D for an invalid reading."""
    try:
        quality = float(value)
    except (ValueError, TypeError, OverflowError):
        return "N/D"
    if not math.isfinite(quality) or quality < 0 or quality > 100:
        return "N/D"
    return f"{quality:.2f}%"


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

def _plot(e, serafini_rows, candles, shift, bounds, reading_clock=None):
    clock_suffix = f" · {reading_clock}" if reading_clock else " · ora non verificata"
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
        column_widths=[0.5,0.25,0.25], horizontal_spacing=0.038,
        subplot_titles=("PREZZO ES · 5 minuti",
                        "SERAFINI NET GAMMA · " + (reading_clock or "ora n.d."),
                        "GEX 0DTE · " + (reading_clock or "ora n.d.")))
    if candles is not None and not candles.empty:
        # Lo stesso asse civile Europe/Rome verificato nel Replay candele:
        # i timestamp originali aware servono per il cutoff, ma l'asse Plotly
        # non deve dipendere dal fuso del browser.
        from replay_candles import _plot_rome_clock
        chart_x = [_plot_rome_clock(stamp) for stamp in candles["bar_close"]]
        source_name = candles.attrs.get("candle_source", "ES futures · 5m")
        fig.add_trace(go.Candlestick(x=chart_x, open=candles["open"],
            high=candles["high"], low=candles["low"], close=candles["close"],
            name=source_name, increasing_line_color="#21b77c",
            decreasing_line_color="#e65353", showlegend=False),row=1,col=1)
    if serafini_rows:
        y = [float(r["strike_spx"])+shift for r in serafini_rows]
        n = [float(r["net_gamma"]) for r in serafini_rows]
        fig.add_trace(go.Bar(x=n,y=y,orientation="h",marker_color=[
            "#22b573" if x>=0 else "#e64b4b" for x in n],
            width=3.6,name="NET Serafini",showlegend=False),row=1,col=2)
        # All three panels share ES coordinates. Display ES price labels,
        # not raw SPX strike labels, to avoid a visible basis displacement.
        # Original SPX strike is preserved unchanged in serafini_rows.
        xmin=min(n)
        xmax=max(n)
        spread=max(xmax-xmin,1.0)
        xbounds=[min(0,xmin)-spread*0.08,max(0,xmax)+spread*0.08]
        label_gap=(xbounds[1]-xbounds[0])*0.012
        selected=[(float(row["strike_spx"]),float(row["net_gamma"]))
                  for row in serafini_rows if float(row["net_gamma"])!=0]
        fig.add_trace(go.Scatter(
            x=[-label_gap if gamma>0 else label_gap for strike,gamma in selected],
            y=[strike+shift for strike,gamma in selected],
            text=[f"{strike+shift:,.2f}".rstrip("0").rstrip(".")
                  for strike,gamma in selected],
            textposition=["middle left" if gamma>0 else "middle right" for strike,gamma in selected],
            mode="text",textfont=dict(color="#e2e8f0",size=10),
            hoverinfo="skip",showlegend=False,cliponaxis=False,
        ),row=1,col=2)
    rows=e["rows"]
    if rows:
        y=[_number(r["strike_spx"])+shift for r in rows]
        for key,name,color in (("call_gex","Call","#22b573"),("put_gex","Put","#e64b4b")):
            fig.add_trace(go.Bar(x=[_number(r[key])/1e9 for r in rows],y=y,orientation="h",
                marker_color=color,width=3.6,name=name,showlegend=False),row=1,col=3)
        fig.add_trace(go.Scatter(x=[_number(r["net_gex"])/1e9 for r in rows],
            y=y,mode="lines",line=dict(color="#f3a2b3",width=1.7),
            name="NET Edge",showlegend=False),row=1,col=3)
    levels=[
        ("Call Wall", e.get("call_wall_spx"), "#31e38b", "dot"),
        ("Put Wall", e.get("put_wall_spx"), "#ff5365", "dot"),
        ("Gamma Flip", e.get("gamma_flip_spx"), "#ffcc4d", "dash"),
    ]
    # Stessi identici livelli SPX+base ES nei tre pannelli. Il pannello
    # a candele mette in evidenza le tre strutture senza cambiare prezzi
    # né scala; gli altri due conservano linee guida più leggere.
    for name, val, color, dash in levels:
        if val is None:
            continue
        y = _number(val) + shift
        if not math.isfinite(y):
            continue
        for col in (1, 2, 3):
            fig.add_hline(
                y=y, line_dash=dash, line_color=color,
                line_width=3 if col == 1 else 1.2,
                opacity=0.98 if col == 1 else 0.6,
                row=1, col=col,
            )
    fig.update_yaxes(range=list(bounds),row=1,col=1,title="Prezzo ES equivalente")
    fig.update_yaxes(range=list(bounds),row=1,col=2)
    fig.update_yaxes(range=list(bounds),row=1,col=3)
    fig.update_layout(height=690,barmode="relative",
        margin=dict(l=40,r=20,t=65,b=50),showlegend=False)
    fig.update_xaxes(type="date", tickformat="%H:%M",
                     title_text="Orario Roma",
                     rangeslider_visible=False, row=1, col=1)
    fig.update_xaxes(title_text="NET Serafini",row=1,col=2)
    fig.update_xaxes(title_text="GEX mld USD/1%",row=1,col=3)
    return fig

@st.cache_data(ttl=120, show_spinner=False, max_entries=18)
def _cached_official_snapshot(week, snapshot, _store):
    """Evita riscaricare il file Cboe completo solo per i KPI Trend Daily."""
    return _store.download(f"{week}/snapshots/{snapshot}")


def _publish_official_header_unchanged(week, store, logs, on_valid_gex):
    """Conserva il GEX di Trend Daily dalla selezione UFFICIALE Serafini.

    Il pannello Replay può essere scelto senza sostituire silenziosamente i
    KPI del Daily con uno snapshot intraday NON ufficiale.
    """
    if on_valid_gex is None:
        return
    ctx = st.session_state.get(f"gamma_compare_context_{week}") or {}
    snapshot = ctx.get("snapshot")
    if not snapshot or ctx.get("offset") is None:
        return
    try:
        day = ctx["day"]
        if isinstance(day, str):
            day = date.fromisoformat(day)
        raw = _cached_official_snapshot(week, snapshot, store)
        from edge_auto import result_from_snapshot
        from edge_daily_gex import prepare_daily_gex
        e = result_from_snapshot(raw, float(ctx["offset"]))
        if (e.get("sha256") != hashlib.sha256(raw).hexdigest()
                or e.get("mode") != "0DTE"
                or e.get("expiries") != [day.isoformat()]):
            return
        from gamma_section import _snapshot_download_clock
        clock = _snapshot_download_clock(store, week, logs or [], snapshot)
        if e.get("quality_sufficient") is True:
            on_valid_gex(prepare_daily_gex(e, day, clock))
    except (RuntimeError, ValueError, KeyError, TypeError, OverflowError,
            OSError, ImportError):
        return


def show(week, store, logs=None, *, on_valid_gex=None):
    with st.expander("GEX EDGE — CONFRONTO", expanded=False):
        from edge_latest_source import latest_gex_reading
        try:
            latest = latest_gex_reading(week, store, logs or [])
        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            st.warning(f"Archivio GEX non verificabile: {exc}")
            return
        if latest is None:
            st.info("Nessun rilevamento GEX verificato disponibile nella settimana.")
            return
        st.markdown(
            f"**Ultimo rilevamento GEX · {latest['kind'].upper()} · "
            f"{latest['clock']:%d/%m/%Y %H:%M:%S} Roma**"
        )
        st.caption(
            "Selezione automatica tra Replay intraday e download Cboe ufficiali, "
            "in base all'orario effettivo verificato. Il metodo NET GAMMA "
            "Serafini resta indipendente."
        )
        # Laboratorio indipendente, DISATTIVATO di default: non cambia KPIs,
        # curve, acquisizioni o selettori delle sezioni Edge gia' operative.
        with st.container(border=True):
            st.markdown("**🧪 LABORATORIO DI RICERCA — NET GAMMA vs GEX**")
            if st.toggle("Apri confronto Serafini · Solo OI · GEX modellato",
                         value=False, key=f"gamma_three_models_enable_{week}"):
                try:
                    from research_gamma_three_models_ui import render as render_three
                    render_three(week, store, logs or [])
                except Exception as exc:
                    st.warning("Laboratorio comparativo non disponibile: "
                               + type(exc).__name__)
        if latest["kind"] == "Replay":
            _publish_official_header_unchanged(
                week, store, logs or [], on_valid_gex)
            from edge_replay_panel import show_replay
            show_replay(week, store, _plot, logs=logs or [], selected=latest)
            return

        # Latest official chain: calculate both profiles on exactly the same
        # original bytes. No active Serafini historical selection is reused.
        from edge_replay_panel import _basis
        from edge_auto import result_from_snapshot
        from net_gamma import read_snapshot, profile
        day = latest["day"]
        selected_name = latest["filename"]
        full_clock = latest["clock"]
        reading_clock = full_clock.strftime("%H:%M:%S")
        shift = _basis(week, day)
        if shift is None:
            st.warning(
                "Differenziale ES−SPX verificato non disponibile per il giorno "
                "dell'ultimo download: coordinate ES GEX sospese."
            )
            return
        try:
            raw = _cached_official_snapshot(week, selected_name, store)
            digest = hashlib.sha256(raw).hexdigest()
            e = result_from_snapshot(raw, float(shift))
            snapshot = read_snapshot(raw)
            reference = float(snapshot["data"]["prev_day_close"])
            if (not math.isfinite(reference) or reference <= 0
                    or e.get("sha256") != digest
                    or e.get("symbol") != "SPX"
                    or e.get("mode") != "0DTE"
                    or e.get("expiries") != [day.isoformat()]):
                raise ValueError("Identità o scadenza della catena 0DTE non coerente")
            serafini = profile(snapshot, day, reference)
            sr = serafini["rows"]
            if not sr:
                raise ValueError("Profilo Serafini vuoto per la stessa fotografia")
        except (RuntimeError, ValueError, TypeError, KeyError,
                OverflowError, OSError) as exc:
            st.warning(f"Download Cboe più recente non verificabile: {exc}")
            return
        st.markdown(f"**Fotografia Cboe ore {reading_clock} Roma**" if reading_clock else
                    "**Fotografia Cboe — orario del download non disponibile**")
        # SPX last-trade timestamp is distinct from download and model times.
        cash_trade_time = extract_spx_trade_time(raw)
        cash_note, _ = describe_spx_trade(cash_trade_time)
        st.caption(cash_note)
        price=float(e["spot_spx"])+shift
        p=st.columns(5)
        p[0].metric("SPX Cboe + base · riferimento", f"{price:,.2f}")
        st.caption(
            f"Prezzo SPX nel JSON Cboe {float(e['spot_spx']):,.2f} "
            f"+ differenziale ES−SPX {shift:+.2f}. "
            "È una conversione teorica degli strike, NON una quotazione "
            "corrente del future ES. Qualità Edge non significa spot aggiornato."
        )
        from gex_price_sensitivity_lab import _gex_reference_times
        _, valuation_text = _gex_reference_times(full_clock, e.get("valuation"))
        st.markdown(f"**Valutazione modello GEX: {valuation_text}**")
        for c,key,lab in zip(p[1:4],("put_wall_spx","gamma_flip_spx","call_wall_spx"),("Put Wall","Gamma Flip","Call Wall")):
            v=e.get(key);c.metric(lab,f"{float(v)+shift:,.2f}" if v is not None else "N/D")
        _colored_edge_metric(p[4], "Regime Edge", str(e.get("regime", "N/D")), e.get("regime"))

        # This is the complete 0DTE Edge chain (not just the strike bars
        # shown +/-10% on the chart). Source snapshot SHA-256 and selected
        # expiry are already checked above against the Serafini analysis.
        st.markdown("**GEX EDGE 0DTE — esposizioni modellate**")
        call_exposure = e.get("call_gex")
        put_exposure = e.get("put_gex")
        sign_label = e.get("modeled_gamma_sign")
        if call_exposure is not None and put_exposure is not None and sign_label:
            # Only publish the SAME verified Edge metrics to Trend Daily.
            # A transient callback is passed by app.py for this rerun: no
            # session_state value can survive a failed later Cboe download.
            if on_valid_gex is not None and e.get("quality_sufficient") is True:
                from edge_daily_gex import prepare_daily_gex
                try:
                    duplicate = prepare_daily_gex(e, day, reading_clock)
                except (ValueError, KeyError, TypeError):
                    pass
                else:
                    on_valid_gex(duplicate)
            columns = st.columns(4)
            # Il segno modellato è già mostrato da "Regime Edge".
            # Qui forniamo invece la qualità effettiva del modello.
            quality = e.get("quality_pct")
            columns[0].metric(
                "Qualità Edge",
                _format_edge_quality(quality),
            )
            _colored_edge_metric(
                columns[1], "NET GEX",
                format_gex_billions(e["net_gex"], signed=True), e.get("net_gex"))
            columns[2].metric(
                "Call GEX", format_gex_billions(call_exposure, signed=True))
            columns[3].metric(
                "Put GEX", format_gex_billions(put_exposure, signed=True))
            st.caption(
                "Unità: miliardi USD di variazione dell'esposizione delta "
                "modellata per un movimento dell'1% di SPX. Call positive e "
                "Put negative sono ipotesi sulle posizioni dei dealer, non "
                "posizioni osservate. Segno Gamma ≠ previsione direzionale. "
                "Scadenza 0DTE e fotografia uguali al confronto Serafini.")
        else:
            st.info("Scomposizione Call/Put non disponibile nel report "
                    "Edge selezionato; nessun valore ricostruito dalle "
                    "sole barre del grafico.")
        try:
            from replay_prices import candles_up_to
            candles=load_es_candles(day, required_until=full_clock)
            candles=candles_up_to(candles, full_clock)
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
        fig=_plot(e,sr,candles,shift,bounds,reading_clock=reading_clock)
        st.plotly_chart(fig,width="stretch",key="edge_compare_three_charts")
        st.markdown(
            "**Legenda GEX:** 🟢 **Call GEX** (barre verdi) · 🔴 **Put GEX** (barre rosse) · "
            "**NET GEX** (linea rosa) · "
            "**Call Wall** (puntinato verde) · **Put Wall** (puntinato rosso) · "
            "**Gamma Flip** (tratteggio giallo). Linee più marcate sul prezzo ES."
        )
        # Same latest official snapshot as the three aligned graphs;
        # no second source selector, no repeated standalone curve.
        from gex_price_sensitivity_lab import show_price_lab
        from edge_replay_panel import _latest_es_quote
        current_es = None
        if day == datetime.now(ZoneInfo("Europe/Rome")).date():
            try:
                current_es = _latest_es_quote(day)
            except (ValueError, RuntimeError, TypeError, KeyError, OSError):
                pass
        show_price_lab(
            e, shift,
            key=f"edge_official_scenario_{week}_{day}_{selected_name}",
            download_clock=full_clock, slot=reading_clock,
            source_kind="Ufficiale", current_es=current_es,
        )

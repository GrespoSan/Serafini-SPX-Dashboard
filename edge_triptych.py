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
        on_valid_gex(prepare_daily_gex(e, day, clock))
    except (RuntimeError, ValueError, KeyError, TypeError, OverflowError,
            OSError, ImportError):
        return


def show(week, store, logs=None, *, on_valid_gex=None):
    with st.expander("GEX EDGE — CONFRONTO", expanded=False):
        source_mode = st.radio(
            "Fonte dei rilevamenti GEX",
            ("Replay intraday", "Download ufficiali"),
            horizontal=True, key=f"edge_compare_source_{week}",
            help="Replay: report Edge indipendenti calcolati dalla stessa catena "
                 "Cboe e archiviati agli orari intraday. Ufficiali: metodo "
                 "preesistente, che rimane disponibile.",
        )
        if source_mode == "Replay intraday":
            # GEX Trend Daily resta basato sulla fotografia UFFICIALE
            # selezionata nel pannello Serafini (nessuna contaminazione).
            _publish_official_header_unchanged(
                week, store, logs or [], on_valid_gex)
            from edge_replay_panel import show_replay
            show_replay(week, store, _plot, logs=logs or [])
            return
        st.caption("Download ufficiali: calcolo Edge e NET GAMMA sulla stessa "
                   "catena Cboe verificata. La selezione precedente è preservata.")
        reading_clock = None
        ctx=st.session_state.get(f"gamma_compare_context_{week}")
        reading=st.radio("Rilevamento da confrontare",("Ultimo rilevamento","Mattina 07:30"),horizontal=True,key=f"edge_compare_reading_{week}")
        # Modalità automatica fissa; l’importazione JSON resta disponibile nel codice.
        source="Automatico · stessa fotografia Serafini"
        if source.startswith("Automatico"):
            if not ctx or not ctx.get("snapshot"):
                st.info("Apri NET GAMMA 0DTE / Weekly e seleziona la fotografia da confrontare.")
                return
            if ctx.get("offset") is None:
                st.warning("Differenziale ES−SPX non disponibile per la fotografia selezionata.")
                return
            try:
                from edge_auto import result_from_snapshot
                day_for_choice=ctx["day"]
                if isinstance(day_for_choice,str):
                    day_for_choice=date.fromisoformat(day_for_choice)
                candidates=sorted(
                    item["name"] for item in store.list(f"{week}/snapshots/")
                    if item.get("name","").startswith("SPX_CBOE_"+day_for_choice.strftime("%Y%m%d")+"_")
                )
                if not candidates:
                    st.warning("Nessuna fotografia della giornata selezionata disponibile.");return
                if reading=="Mattina 07:30":
                    from gamma_section import _acquisition_label
                    morning=[name for name in candidates if _acquisition_label(store,week,logs or [],name)=="Mattina 07:30"]
                    if not morning:
                        st.warning("Non è presente una fotografia identificata dal registro come Mattina 07:30.");return
                    selected_name=morning[-1]
                else:
                    # Names contain the original CBOE timestamp, not the download time.
                    # Prefer the latest logged download to avoid misleading ordering.
                    from datetime import timezone
                    from zoneinfo import ZoneInfo
                    import json
                    registered=[]
                    for log in logs or []:
                        try:
                            info=json.loads(store.download(f"{week}/logs/{log['name']}"))
                            source_file=str(info.get("file","")).rsplit("/",1)[-1]
                            raw_time=info.get("download_rome") or info.get("download_utc")
                            if source_file not in candidates or not raw_time:
                                continue
                            timestamp=datetime.fromisoformat(str(raw_time).replace("Z","+00:00"))
                            if timestamp.tzinfo is None:
                                continue
                            registered.append((timestamp.astimezone(timezone.utc),source_file))
                        except (TypeError,ValueError,KeyError):
                            continue
                    if not registered:
                        st.warning("I registri di acquisizione non indicano l'ultimo download: impossibile identificarlo con certezza.");return
                    selected_name=max(registered)[1]
                raw=store.download(f"{week}/snapshots/{selected_name}")
                e=result_from_snapshot(raw,float(ctx["offset"]))
                from gamma_section import _snapshot_download_clock
                download_clock=_snapshot_download_clock(store,week,logs or [],selected_name)
                reading_clock = download_clock
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
        st.markdown(f"**Fotografia Cboe ore {reading_clock} Roma**" if reading_clock else
                    "**Fotografia Cboe — orario del download non disponibile**")
        # GEX only: read the ORIGINAL Cboe index timestamp, not option
        # quote times and not the model/download timestamp.
        cash_trade_time = extract_spx_trade_time(raw) if source.startswith("Automatico") else None
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
        if e.get("valuation"):
            st.caption(f"Valutazione del modello Edge: {e['valuation']} "
                       "(orario del modello, diverso dal download).")
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
            if on_valid_gex is not None:
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
        fig=_plot(e,sr,candles,shift,bounds,reading_clock=reading_clock)
        st.plotly_chart(fig,width="stretch",key="edge_compare_three_charts")
        st.markdown(
            "**Legenda GEX:** 🟢 **Call GEX** (barre verdi) · 🔴 **Put GEX** (barre rosse) · "
            "**NET GEX** (linea rosa) · "
            "**Call Wall** (puntinato verde) · **Put Wall** (puntinato rosso) · "
            "**Gamma Flip** (tratteggio giallo). Linee più marcate sul prezzo ES."
        )
        with st.expander("Curva teorica GEX",expanded=False):
            curve=e.get("curve",[])
            curve_title = (f"Curva teorica GEX 0DTE — fotografia Cboe ore {reading_clock} Roma"
                           if reading_clock else "Curva teorica GEX 0DTE — orario del rilevamento non disponibile")
            if curve:
                # Estremi ricavati dagli stessi punti mostrati nel grafico.
                valid_points = [
                    (_number(r["spot_spx"]) + shift, _number(r["net_gex"]) / 1e9)
                    for r in curve
                ]
                valid_points = [(x, y) for x, y in valid_points
                                if math.isfinite(x) and math.isfinite(y)]
                if valid_points:
                    max_point = max(valid_points, key=lambda point: point[1])
                    min_point = min(valid_points, key=lambda point: point[1])
                    st.markdown(
                        f"**{curve_title} · Max {max_point[1]:+.2f} mld (ES {max_point[0]:,.2f})"
                        f" · Min {min_point[1]:+.2f} mld (ES {min_point[0]:,.2f})**"
                    )
                    f=go.Figure(go.Scatter(x=[p[0] for p in valid_points],
                                           y=[p[1] for p in valid_points],mode="lines"))
                    f.add_hline(y=0, line_dash="dash", line_color="#f4c95d", line_width=1.5)
                    st.plotly_chart(f,width="stretch")
                    st.markdown(
                        "**Come leggere la curva teorica GEX**\n\n"
                        "**A cosa serve?** Se l'SPX raggiungesse un determinato prezzo, "
                        "come cambierebbe l'esposizione Gamma complessiva delle opzioni 0DTE considerate? "
                        "La curva mostra il GEX ricalcolato per diversi prezzi ipotetici "
                        "partendo dalla stessa fotografia Cboe, senza ipotizzare nuove operazioni "
                        "o variazioni delle posizioni aperte.\n\n"
                        "**Sopra lo zero (GEX positivo):** in determinate condizioni, "
                        "le coperture dei dealer possono contrastare i movimenti del prezzo, "
                        "favorendo una maggiore stabilità. "
                        "**Sotto lo zero (GEX negativo):** le coperture possono invece "
                        "amplificare i movimenti e la volatilità.\n\n"
                        "**Il passaggio attraverso lo zero** identifica un Gamma Flip teorico. "
                        "La curva non prevede la direzione del mercato e non costituisce "
                        "un segnale automatico di acquisto o vendita."
                    )
                else:
                    st.markdown(f"**{curve_title}**")
            else:
                st.markdown(f"**{curve_title}**")

        # Also offer the laboratory on a selected OFFICIAL Edge snapshot.
        # This never mixes official exposure with any Replay sidecar.
        from gex_price_sensitivity_lab import show_price_lab
        from edge_replay_panel import _latest_es_quote
        from gamma_section import _snapshot_log_index
        idx = _snapshot_log_index(
            week, tuple(str(item["name"]) for item in (logs or [])), store)
        source_row = idx.get(f"{week}/snapshots/{selected_name}")
        full_clock = source_row[2] if source_row else None
        source_label = source_row[0] if source_row else ""
        logical_slot = (
            "17:00" if source_label.startswith("USA · 10:00")
            else "15:50" if source_label.startswith("Pomeriggio ·")
            else "07:30" if source_label.startswith("Mattina")
            else "Rilevamento ufficiale"
        )
        current_es = None
        if day == datetime.now(ZoneInfo("Europe/Rome")).date():
            try:
                current_es = _latest_es_quote(day)
            except (ValueError, RuntimeError, TypeError, KeyError, OSError):
                pass
        show_price_lab(
            e, shift,
            key=f"edge_official_scenario_{week}_{day}_{selected_name}",
            download_clock=full_clock,
            slot=logical_slot, source_kind="Ufficiale",
            current_es=current_es,
        )

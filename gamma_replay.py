"""Replay temporaneo intraday: 5 snapshot ufficiali + replay intraday compatto.

Il grafico espone ORARI DI DOWNLOAD a Roma; il timestamp Cboe non viene
interpretato come tempo UTC o come ora italiana. Nessuna scrittura in Supabase.
"""
import json
import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from net_gamma import profile, read_snapshot, visual_profile
from feed_quality import summarize_timeline
from replay_scale import dynamic_gamma_bounds
from replay_profiles import choose_rows, gamma_bar_trace
from replay_view import plotted_rows, price_zoom_bounds, coverage_note
from acquisition_schedule import (
    ALL_SLOTS, ALWAYS_OFFICIAL, LEGACY_1330_CRON,
    FIRST_1330_OFFICIAL, official_slots_for_day,
    raw_study_1330_for_day, expected_replay_slots,
)
from spx_market_clock import classify_0dte_download, cash_open_rome, spxw_expiry_close_rome

ROME = ZoneInfo("Europe/Rome")
SLOTS = ALL_SLOTS
OFFICIAL = {**ALWAYS_OFFICIAL, "14:46": "46 14 * * 1-5"}
# Compatibilità storica per gli importatori esistenti; classificare sempre
# utilizzando official_slots_for_day(day), non la costante OFFICIAL.
OFFICIAL_1330_START = FIRST_1330_OFFICIAL

def weekly_expiry(day):
    from datetime import timedelta
    delta = 4 - day.weekday()
    return day + timedelta(days=delta if delta > 0 else 7)



def _download_clock(record):
    value = record.get("download_rome") or record.get("download_utc")
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(ROME)
    except (ValueError, TypeError, OverflowError):
        return None


@st.cache_data(ttl=90, show_spinner=False, max_entries=12)
def _official_frames(_store, week, day, logs):
    """Preferisci il primo evento valido nello slot ufficiale."""
    by_slot = {}
    for log in logs:
        try:
            item = json.loads(_store.download(f"{week}/logs/{log['name']}"))
            slots_for_day = official_slots_for_day(day)
            if raw_study_1330_for_day(day):
                slots_for_day["13:30"] = LEGACY_1330_CRON
            slot = next((k for k, v in slots_for_day.items()
                         if item.get("scheduled_slot") == v), None)
            if (not slot or item.get("rome_date") != day.isoformat()
                    or item.get("status") not in ("NUOVO", "INVARIATO")
                    or not item.get("file")):
                continue
            clock = _download_clock(item)
            if not clock:
                continue
            # Un log ufficiale puo' indicare una catena senza Gamma utilizzabili
            # (es. vecchi download). La verifica completa e' sotto.
            by_slot.setdefault(slot, []).append((clock, item))
        except (KeyError, ValueError, TypeError, RuntimeError, OverflowError):
            continue

    result = {}
    for slot, candidates in by_slot.items():
        for clock, item in sorted(candidates, key=lambda pair: pair[0]):
            try:
                raw_cboe = _store.download(item["file"])
                snap = read_snapshot(raw_cboe)
                ref = float(snap["data"].get("prev_day_close") or 0)
                if ref <= 0:
                    continue
                profiles = {}
                for kind, expiry in (("0DTE", day), ("Weekly", weekly_expiry(day))):
                    calculated = profile(snap, expiry, ref)
                    if not calculated.get("rows"):
                        raise ValueError("Profilo vuoto")
                    # L'estensione e' facoltativa: un errore fuori dai 50
                    # strike originali non puo' scartare il rilevamento
                    # ufficiale ne' impedire l'esportazione Pine.
                    try:
                        expanded = visual_profile(
                            snap, calculated, expiry, ref,
                            snap["data"].get("current_price"))
                        visual_rows = expanded["rows"]
                        visual_complete = expanded["coverage_complete"]
                    except (ValueError, TypeError, KeyError, OverflowError):
                        visual_rows = calculated["rows"]
                        visual_complete = False
                    profiles[kind] = {
                        "rows": [
                            {"strike_spx": r["strike_spx"], "net_gamma": r["net_gamma"]}
                            for r in calculated["rows"]],
                        # Fallback senza inventare strike o gamma:
                        # esattamente i 50 originali della stessa catena.
                        "visual_rows": [
                            {"strike_spx": r["strike_spx"], "net_gamma": r["net_gamma"]}
                            for r in visual_rows],
                        "visual_coverage_complete": visual_complete,
                    }
                    # Catena ufficiale completa già caricata: il Replay può
                    # estendere lo zoom ±10% senza un secondo download.
                    # In caso di problemi del pool, preserva i 50+140 originali.
                    try:
                        from replay_view import full_pool_from_archived_cboe
                        profiles[kind].update(full_pool_from_archived_cboe(
                            snap, expiry, ref, profiles[kind]["rows"],
                            snap["data"].get("current_price")))
                    except (ValueError, TypeError, KeyError, OverflowError):
                        pass
                if not any(abs(r["net_gamma"]) > 0 for r in profiles["0DTE"]["rows"]):
                    continue
                result[slot] = {
                    "slot_rome": slot, "clock": clock.strftime("%H:%M:%S"),
                    "download_rome": clock.isoformat(timespec="seconds"),
                    "origin": ("SLOT UFFICIALE · ALPACA INDICATIVE / GAMMA MODELLATO"
                               if snap.get("source_provenance",{}).get("source") == "ALPACA_INDICATIVE_OCC_OI"
                               else ("STUDIO STORICO DA ARCHIVIO"
                                     if slot == "13:30" and raw_study_1330_for_day(day)
                                     else "UFFICIALE")),
                    "spot": snap["data"].get("current_price"),
                    "reference_spx": ref, "profiles": profiles,
                    "cboe_timestamp_original": snap.get("timestamp"),
                    # Exact file path chosen by the Replay OFFICIAL reader.
                    # Export GEX must use this SAME file, never reselect by
                    # timestamp from a different log or slot.
                    "snapshot_path": item["file"],
                    "snapshot_download_clock": clock.strftime("%H:%M:%S"),
                }
                break
            except (KeyError, TypeError, ValueError, OSError, RuntimeError, OverflowError):
                continue
    return result


@st.cache_data(ttl=90, show_spinner=False, max_entries=12)
def _study_frames(_store, week, day):
    data = {}
    for obj in _store.list(f"replay/{week}/"):
        name = str(obj.get("name", ""))
        if not name.startswith(day.isoformat() + "_") or not name.endswith(".json"):
            continue
        try:
            item = json.loads(_store.download(f"replay/{week}/{name}"))
            if (item.get("schema") != "serafini_replay_v1"
                    or item.get("rome_date") != day.isoformat()
                    or item.get("kind") != "STUDIO_INTRADAY_NON_UFFICIALE"):
                continue
            slot = item.get("slot_rome")
            # 13:30 è Replay, non promuoverlo automaticamente ad ufficiale.
            if slot not in expected_replay_slots(day) or slot in ALWAYS_OFFICIAL:
                continue
            dt = _download_clock(item)
            if dt is None:
                continue
            profiles = item.get("profiles") or {}
            if not all(profiles.get(k, {}).get("rows") for k in ("0DTE", "Weekly")):
                continue
            data[slot] = {
                "slot_rome": slot, "clock": dt.strftime("%H:%M:%S"),
                "download_rome": dt.isoformat(timespec="seconds"),
                "origin": "STUDIO NON UFFICIALE",
                "spot": item.get("spot_spx"),
                "reference_spx": item.get("reference_spx"),
                "cboe_timestamp_original": item.get("cboe_timestamp_original"),
                "profiles": profiles,
            }
        except (KeyError, ValueError, TypeError, RuntimeError, OverflowError):
            continue
    return data


def merge_replay_sources(study, official, day):
    """Keep official and intraday Replay distinct at the coincident 16:16 ID."""
    frames = dict(study)
    if "16:16" in study and "16:16" in official:
        frames["16:16 · Replay"] = study["16:16"]
    for slot, item in official.items():
        if not (slot == "13:30" and raw_study_1330_for_day(day)
                and slot in frames):
            frames[slot] = item
    return frames


def render(store, week, logs, view_mode=None):
    st.subheader("Replay / History — evoluzione NET GAMMA")
    st.caption(
        "Ufficiale pre-USA dalle 12/11: 07:46 Chicago (slot logico 14:46; "
        "14:46 Roma normalmente, 13:46 Roma nel disallineamento DST). "
        "13:30 Roma resta solo Replay; storico 13:30 ufficiale soltanto "
        "l'11/11. Dal 16/11 gli ufficiali 16:16 e 17:46 sono ancorati "
        "alle 09:16 e 10:46 Chicago; il Replay 16:16 resta distinto. "
        "Gli orari effettivi Roma sono registrati nei fotogrammi."
    )
    from datetime import timedelta
    iso_year, iso_week = map(int, week.split("-W"))
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    weekdays = [monday + timedelta(days=i) for i in range(5)]
    current_date = datetime.now(ROME).date()
    default_day = current_date if current_date in weekdays else weekdays[0]
    day = st.selectbox("Giornata replay", weekdays,
                       index=weekdays.index(default_day),
                       format_func=lambda d: d.strftime("%d/%m/%Y"),
                       key=f"replay_day_{week}")
    # Ogni profilo conserva scadenza e catena Cboe proprie. Weekly è
    # visualizzabile ma NON deve usare il filtro post-scadenza 0DTE.
    kind = st.radio(
        "Scadenza Replay", ("0DTE", "Weekly"), horizontal=True,
        key=f"replay_expiry_{week}_{day}", index=0,
    )
    if view_mode is None:
        view_mode = st.radio("Visualizzazione", ("Solo NET GAMMA", "Candele ES + NET GAMMA"),
                             horizontal=True, key=f"replay_visual_{week}")
    elif view_mode not in ("Solo NET GAMMA", "Candele ES + NET GAMMA"):
        raise ValueError("Modalità Replay non valida")
    with st.spinner("Caricamento profili compatti e snapshot ufficiali..."):
        # At 09:16 Chicago the official (logical 16:16) can coexist with
        # the original nonofficial 16:16 Replay. Preserve BOTH originals
        # and order by their real capture clocks, never by logical names.
        frames = merge_replay_sources(
            _study_frames(store, week, day),
            _official_frames(store, week, day, logs), day)
    expected_slots = expected_replay_slots(day)
    if "16:16 · Replay" in frames:
        items = list(expected_slots)
        items.insert(items.index("16:16") + 1, "16:16 · Replay")
        expected_slots = tuple(items)
    all_available = [(slot, frames[slot]) for slot in expected_slots if slot in frames
                     and frames[slot].get("profiles", {}).get(kind, {}).get("rows")]
    # Ordinare secondo il download EFFETTIVO e non secondo la stringa dello
    # slot: nei disallineamenti DST il 17:00 logico USA cade alle 16:00 Roma.
    all_available.sort(key=lambda pair: _download_clock(pair[1]) or
                       datetime.max.replace(tzinfo=ROME))
    post_expiry = [
        (slot, frame) for slot, frame in all_available
        if classify_0dte_download(day, frame.get("download_rome"))["post_expiry"]
    ]
    # Il filtro di scadenza è applicato SOLO al 0DTE.
    # Weekly resta valida anche dopo il close SPXW 0DTE.
    available = [(slot, frame) for slot, frame in all_available
                 if kind == "Weekly" or not classify_0dte_download(
                     day, frame.get("download_rome"))["post_expiry"]]
    missing = [slot for slot in expected_slots if slot not in frames]
    if kind == "0DTE" and post_expiry:
        ignored = ', '.join(slot for slot, _ in post_expiry)
        limit = classify_0dte_download(
            day, post_expiry[0][1]["download_rome"])["expiry_close_rome"][11:16]
        st.warning(
            f"0DTE: il rilevamento {ignored} e' POST-SCADENZA SPXW. "
            f"Chiusura 16:00 New York = {limit} Roma in questa data. "
            "Escluso da Replay e GIF 0DTE, ma conservato nel report "
            "integrale e utilizzabile nel Weekly."
        )
    if kind == "Weekly":
        expiry_selected = weekly_expiry(day)
        st.caption(f"Weekly osservata: scadenza {expiry_selected:%d/%m/%Y}. "
                   "Il venerdi si passa alla settimana successiva; non confrontare "
                   "direttamente giornate con scadenze Weekly differenti.")
    if not available:
        st.info("Nessun frame replay disponibile per questa data. I frame futuri "
                "non possono essere ricostruiti retroattivamente.")
        return
    if len(available) == 1:
        st.warning("Replay non animabile: e' disponibile soltanto il frame "
                   f"{available[0][0]}. Play richiede almeno due acquisizioni "
                   "distinte. Gli snapshot intermedi del 20/10 non possono "
                   "essere ricreati retroattivamente: il replay temporaneo "
                   "e' stato attivato successivamente.")


    # I 50 strike Serafini, i 140 legacy e il nuovo pool grafico Cboe
    # restano dati distinti. Questa fascia cambia SOLO il Replay e la GIF.
    display_mode = st.radio(
        "Copertura NET GAMMA nel Replay",
        ("Estesa · strike Cboe disponibili", "Originale Serafini · 50 strike"),
        horizontal=True, index=0,
        key=f"replay_gamma_coverage_{week}_{day}_{kind}",
        help="Gli strike estesi provengono dalla stessa fotografia Cboe; "
             "nessun calcolo dei 50 Serafini viene alterato.",
    )
    show_extended = display_mode.startswith("Estesa")
    band_pct = st.number_input(
        "Fascia strike ± % dal Close precedente (0DTE e Weekly)",
        min_value=0.5, max_value=10.0, value=3.0, step=0.5,
        key=f"replay_strike_band_{week}_{day}_{kind}",
        disabled=not show_extended,
        help="Sceglie fra i veri strike archiviati. Per vecchi Replay compatti "
             "non può recuperare dati Cboe mai salvati.",
    )
    auto_extend = st.checkbox(
        "Estensione automatica NET GAMMA — solo grafici e GIF",
        value=True, disabled=not show_extended,
        key=f"replay_auto_extend_{week}_{day}_{kind}",
        help="Se lo spot della fotografia esce dalla fascia, include "
             "un margine di ±0,6% del close precedente attorno allo spot. "
             "Non cambia i 50 strike Serafini né il Pine ufficiale.",
    )
    zoom_mode = st.radio(
        "Zoom verticale del Replay",
        ("Automatico · segue il prezzo", "Fascia completa · mostra gli strike"),
        horizontal=True, index=0,
        key=f"replay_price_zoom_{week}_{day}_{kind}",
        help="Automatico: segue la giornata e mantiene la scala identica "
             "durante Play. Fascia completa: visualizza tutta la "
             "copertura degli strike selezionati.",
    )
    show_full_band = zoom_mode.startswith("Fascia completa")

    def display_rows(item):
        return plotted_rows(
            item["profiles"][kind], item["reference_spx"], item.get("spot"),
            band_pct, auto_extend, show_extended,
        )

    # Unica fonte: differenziale della medesima data gia' calcolato/impostato
    # in ANALISI NET GAMMA. Nessun input o calcolo parallelo nel Replay.
    offset_key = f"basis_override_{day.isoformat()}"
    offset_text = str(st.session_state.get(offset_key, "")).strip()
    if not offset_text:
        cached = st.session_state.get("auto_basis_results", {}).get(day.isoformat())
        if cached is not None:
            try:
                from decimal import Decimal, ROUND_HALF_UP
                offset_text = str(int(Decimal(str(cached["basis"])).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP)))
            except (ValueError, TypeError, KeyError, ArithmeticError):
                offset_text = ""
    try:
        offset = float(offset_text.replace(",", ".")) if offset_text else None
        if offset is not None and not math.isfinite(offset):
            offset = None
    except ValueError:
        offset = None
    if offset is None:
        st.info("Differenziale ES − SPX non disponibile per questa giornata. "
                "Apri ANALISI NET GAMMA per calcolarlo o verificarlo; "
                "nel frattempo il Replay mostra gli strike SPX.")

    def numeric_spot(item):
        """Never substitute live prices for a historical Cboe observation."""
        try:
            spot = float(item.get("spot"))
            return spot if math.isfinite(spot) and spot > 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    all_rows = [r for _, f in available for r in display_rows(f)]
    strikes = sorted({float(r["strike_spx"]) for r in all_rows})
    counts = [len(display_rows(frame)) for _, frame in available]
    if show_extended:
        notes = [
            (slot, coverage_note(frame["profiles"][kind],
             frame["reference_spx"], band_pct, auto_extend, frame.get("spot")))
            for slot, frame in available
        ]
        legacy = [slot for slot, note in notes
                  if note["origin"] == "profilo storico limitato"]
        capped = [slot for slot, note in notes if note.get("capped")]
        if legacy:
            st.caption(
                "Archivio storico senza pool ampio: " + ", ".join(legacy) +
                ". La fascia è applicata ai livelli realmente salvati; "
                "non può generare nuovi strike. I nuovi Replay avranno "
                "un pool Cboe fino a ±10% del close precedente."
            )
        if capped:
            st.caption(
                "Pool Cboe troncato per limite di archiviazione (600 strike): "
                + ", ".join(capped) + ". Nessun livello interpolato."
            )
    # Scala adattiva per frame, ma simmetrica: il NET GAMMA zero rimane
    # esattamente al centro della stessa posizione in ogni rilevamento.
    # I valori NET GAMMA originali restano invariati.
    def frame_bounds(item):
        return dynamic_gamma_bounds(
            [r["net_gamma"] for r in display_rows(item)])

    bounds = frame_bounds(available[0][1])

    # Axes remain numeric: a price line must represent its exact value even
    # when the price falls between two available option strikes.
    yvalues = [strike + (offset or 0) for strike in strikes]
    prices = [(numeric_spot(item) + (offset or 0))
              for _, item in available if numeric_spot(item) is not None]
    reference_price = float(available[0][1]["reference_spx"]) + (offset or 0)
    ybounds = price_zoom_bounds(
        prices, [], [], yvalues, reference_price, complete=show_full_band,
    )

    def top3_guides(item):
        """Tre NET positivi e tre NET negativi, senza stimare strike assenti."""
        ranked = [(float(r["strike_spx"]), float(r["net_gamma"]))
                  for r in display_rows(item)]
        positives = sorted((p for p in ranked if p[1] > 0),
                           key=lambda p: (-p[1], p[0]))[:3]
        negatives = sorted((p for p in ranked if p[1] < 0),
                           key=lambda p: (p[1], p[0]))[:3]
        return ([p[0] + (offset or 0) for p in positives]
                + [None] * (3 - len(positives))
                + [p[0] + (offset or 0) for p in negatives]
                + [None] * (3 - len(negatives)))

    def guide_traces(item):
        left, right = frame_bounds(item)
        result = []
        for index, price in enumerate(top3_guides(item)):
            positive = index < 3
            result.append(go.Scatter(
                x=[left, right] if price is not None else [None, None],
                y=[price, price] if price is not None else [None, None],
                mode="lines", line=dict(color="#22c55e" if positive else "#ef4444",
                                        width=1, dash="dash"),
                visible=False, hoverinfo="skip", showlegend=False,
            ))
        return result

    def frame_data(slot, item):
        vals = {float(r["strike_spx"]): float(r["net_gamma"])
                for r in display_rows(item)}
        # Il helper crea realmente il go.Bar per il Replay (e i frame).
        # Nessun gamma=0 inventato per strike assenti: usa x=None con un
        # colore trasparente Plotly valido, rgba(0,0,0,0).
        bars = gamma_bar_trace(slot, strikes, yvalues, vals)
        # Le etichette visualizzano la STESSA coordinata verticale delle barre:
        # prezzo ES (strike SPX + basis), oppure strike SPX se manca il basis.
        # Non si mostra il valore numerico NET GAMMA come etichetta:
        # rimane disponibile passando il mouse sulla barra.
        # Su 100-140 barre le etichette complete si sovrapporrebbero:
        # privilegiare i 32 livelli per |NET Gamma|, tutte le barre restano
        # leggibili dal tooltip e i valori originali NON sono modificati.
        ranked = sorted(
            ((strike, gamma) for strike, gamma in vals.items() if gamma != 0),
            key=lambda p: (-abs(p[1]), p[0]),
        )
        selected = sorted((ranked[:32] if show_extended else ranked),
                          key=lambda pair: pair[0])
        current_bounds = frame_bounds(item)
        label_gap = (current_bounds[1] - current_bounds[0]) * 0.012
        labels = go.Scatter(
            x=[-label_gap if gamma > 0 else label_gap
               for strike, gamma in selected],
            y=[strike + (offset or 0) for strike, gamma in selected],
            text=[f"{strike + (offset or 0):,.2f}".rstrip("0").rstrip(".")
                  for strike, gamma in selected],
            textposition=["middle left" if gamma > 0 else "middle right"
                          for strike, gamma in selected],
            mode="text", textfont=dict(color="#e2e8f0", size=10),
            hoverinfo="skip", showlegend=False, cliponaxis=False,
        )
        # Sei guide opzionali: aggiornate al frame senza alterare le barre.
        return [bars, labels, *guide_traces(item)]

    def frame_title(slot, item):
        spot = numeric_spot(item)
        spot_text = f"SPX Cboe {spot:,.2f}" if spot is not None else "SPX non disponibile"
        es_text = (f" | ES stimato {spot + offset:,.2f}"
                   if spot is not None and offset is not None else "")
        cboe_time = str(item.get("cboe_timestamp_original") or "n.d.")
        return (f"<span style='font-size:20px;color:#55D5F6'><b>"
                f"NET GAMMA {kind} · {day:%d/%m/%Y} · "
                f"Rilevamento {slot} · download {item['clock']} Roma"
                f"</b></span>"
                f"<br><sup>{spot_text}{es_text} | Timestamp Cboe: {cboe_time} "
                f"(fuso non dichiarato)</sup>")

    def frame_animation_data(slot, item):
        traces = frame_data(slot, item)
        # Il toggle resta attivo durante Play e spostamento dello slider:
        # la visibilità appartiene al grafico, non al singolo frame.
        for guide in traces[2:]:
            guide.visible = None
        return traces

    first_slot, first = available[0]
    graph = go.Figure(data=frame_data(first_slot, first))
    graph.frames = [
        go.Frame(name=slot, data=frame_animation_data(slot, item), traces=list(range(8)),
                 layout=go.Layout(title={"text": frame_title(slot, item)},
                                  xaxis=dict(range=frame_bounds(item))))
        for slot, item in available
    ]
    steps = [
        dict(method="animate", label=slot,
             args=[[slot], {"mode": "immediate",
                            "frame": {"duration": 0, "redraw": True},
                            "transition": {"duration": 0}}])
        for slot, _ in available
    ]
    graph.update_layout(
        title={"text": frame_title(first_slot, first)},
        template="plotly_dark",
        height=max(650, min(1000, 18 * len(strikes) + 175)),
        margin=dict(l=16, r=16, t=94, b=90),
        xaxis=dict(title="NET GAMMA", range=bounds, zeroline=True, fixedrange=True),
        yaxis=dict(title="Prezzo ES stimato" if offset is not None else "Strike SPX",
                   range=ybounds, fixedrange=True, tickformat=",.0f"),
        updatemenus=([dict(type="buttons", showactive=False, direction="left",
                 x=0.01, y=-0.12,
                 buttons=[
                     dict(label="▶ Play", method="animate",
                          args=[None, {"fromcurrent": True,
                                       "frame": {"duration": 1100, "redraw": True},
                                       "transition": {"duration": 0}}]),
                     dict(label="Ⅱ Pausa", method="animate",
                          args=[[None], {"mode": "immediate",
                                         "frame": {"duration": 0, "redraw": False},
                                         "transition": {"duration": 0}}]),
                 ])] if len(available) > 1 else []),
        sliders=([dict(active=0, x=0.26, y=-0.10, len=0.72,
                 currentvalue={"prefix": "Orario: "}, steps=steps)]
                 if len(available) > 1 else []),
    )
    # Comando separato accanto a Play/Pausa, ON/OFF senza rerun Streamlit.
    # Gli indici delle guide per il profilo singolo sono 2..7.
    guide_indices = list(range(2, 8))
    guide_menu = dict(
        # Evita il fondo bianco di Plotly sul pulsante attivo, che rende
        # il testo chiaro illeggibile. Il toggle args/args2 resta operativo.
        type="buttons", showactive=False, active=-1,
        bgcolor="#334155", bordercolor="#64748b",
        font=dict(color="#ffffff", size=12),
        direction="left", x=0.135, y=-0.12,
        buttons=[dict(
            label="Top 3 + / −", method="restyle",
            args=[{"visible": True}, guide_indices],
            args2=[{"visible": False}, guide_indices],
        )],
    )
    graph.update_layout(updatemenus=list(graph.layout.updatemenus or []) + [guide_menu])
    if view_mode == "Candele ES + NET GAMMA":
        if offset is None:
            st.warning("Per affiancare le candele ES occorre il differenziale ES − SPX. "
                       "Senza differenziale i livelli Cboe sono strike SPX, non prezzi ES.")
        else:
            try:
                from replay_candles import combined_graph
                graph = combined_graph(
                    graph, available, day, kind, offset, bounds, ybounds,
                    zoom_complete=show_full_band,
                )
                candle_meta = graph.layout.meta or {}
            except Exception as exc:
                st.warning(f"Candele ES non disponibili ({exc}). Resta visibile il Replay NET GAMMA originale.")
    # Un reset esplicito ripristina la finestra Roma, lo zoom e lo slider
    # ricreando il widget Plotly; NON richiede nuovi snapshot e non tocca
    # dati NET GAMMA, offset o giornata selezionata.
    view_reset_key = f"replay_view_reset_{week}_{day}_{kind}_{view_mode}"
    if st.button("↺ Ripristina vista grafico",
                 key=f"replay_reset_button_{week}_{day}_{kind}_{view_mode}",
                 help="Ripristina asse orario Roma, zoom e rilevamento iniziale, senza cambiare i dati."):
        st.session_state[view_reset_key] = st.session_state.get(view_reset_key, 0) + 1
        st.rerun()
    view_reset_number = st.session_state.get(view_reset_key, 0)
    graph.update_layout(uirevision=f"replay_view_{week}_{day}_{view_reset_number}")
    st.plotly_chart(graph, width="stretch",
                    config={"displayModeBar": True, "doubleClick": "reset"},
                    key=f"replay_chart_{week}_{day}_{kind}_{view_mode}_{view_reset_number}")

    # GIF solo su richiesta: nessuna generazione nei normali rerun del replay.
    # Conserviamo una sola GIF in memoria per sessione e invalidiamo la cache
    # se cambiano giorno, scadenza, differenziale o gli snapshot disponibili.
    gif_signature = ("replay_band_v5", day.isoformat(), kind, offset,
                     show_extended, band_pct, auto_extend, show_full_band,
                     tuple((slot, item.get("clock"),
                            tuple((float(x["strike_spx"]), float(x["net_gamma"]))
                                  for x in display_rows(item)))
                           for slot, item in available))
    gif_key = "replay_gif_last"
    if st.button("Esporta GIF giornata", key=f"gif_prepare_{week}_{day}_{kind}",
                 disabled=offset is None or len(available) < 2,
                 help="Genera solo su richiesta la GIF di tutti i rilevamenti "
                      "disponibili, con le candele ES fino a ciascun download."):
        try:
            from replay_gif import generate_gif
            with st.spinner("Creazione GIF dai rilevamenti disponibili..."):
                gif_data, gif_frames = generate_gif(
                    available, day, kind, offset, show_extended=show_extended,
                    band_pct=band_pct, auto_extend=auto_extend,
                    zoom_complete=show_full_band)
            st.session_state[gif_key] = {
                "signature": gif_signature, "data": gif_data, "count": gif_frames
            }
        except Exception as exc:
            st.session_state.pop(gif_key, None)
            st.warning(f"GIF non disponibile: {str(exc)[:220]}")
    gif_saved = st.session_state.get(gif_key)
    if gif_saved is not None and gif_saved["signature"] == gif_signature:
        st.download_button(
            f"Scarica GIF giornata ({gif_saved['count']} rilevamenti)",
            data=gif_saved["data"],
            file_name=f"NET_GAMMA_{kind}_{day:%Y-%m-%d}.gif",
            mime="image/gif", key=f"gif_download_{week}_{day}_{kind}",
        )

    table = []
    for slot, item in available:
        rows = item["profiles"][kind]["rows"]
        upper = sorted((r for r in rows if float(r["net_gamma"]) > 0),
                       key=lambda r: float(r["net_gamma"]), reverse=True)[:3]
        lower = sorted((r for r in rows if float(r["net_gamma"]) < 0),
                       key=lambda r: float(r["net_gamma"]))[:3]
        fmt = lambda xs: ", ".join(f"{float(r['strike_spx']) + (offset or 0):.0f}" for r in xs) or "—"
        spot = numeric_spot(item)
        table.append({"Slot Roma": slot, "Download Roma": item["clock"],
                      "Timestamp Cboe": item.get("cboe_timestamp_original") or "—",
                      "SPX Cboe": round(spot, 2) if spot is not None else None,
                      "ES stimato": round(spot + offset, 2) if spot is not None and offset is not None else None,
                      "Tipo": item["origin"], "Top 3 positivo": fmt(upper),
                      "Top 3 negativo": fmt(lower)})
    with st.expander("Dettaglio rilevamenti Replay · tabella", expanded=False):
        st.dataframe(table, hide_index=True, width="stretch")



def render_research_lab(store, week, logs, *, inside_section=False):
    """Laboratorio autonomo: riusa gli snapshot del Replay senza il suo grafico.

    Selezione giorno/scadenza indipendente da REPLAY E CONFRONTO; gli archivi
    restano read-only e vengono filtrati con le medesime regole 0DTE/Weekly.
    """
    from datetime import timedelta
    from research_gamma_replay_ui import render as render_gamma_research

    from contextlib import nullcontext
    panel = (nullcontext() if inside_section else
             st.expander("🧪 LABORATORIO · Evoluzione NET GAMMA (sperimentale)",
                         expanded=False))
    with panel:
        iso_year, iso_week = map(int, week.split("-W"))
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        weekdays = [monday + timedelta(days=i) for i in range(5)]
        selected_replay = st.session_state.get(f"replay_day_{week}")
        today = datetime.now(ROME).date()
        preferred = selected_replay if selected_replay in weekdays else (
            today if today in weekdays else weekdays[0])
        day = st.selectbox(
            "Giornata di ricerca NET GAMMA", weekdays,
            index=weekdays.index(preferred),
            format_func=lambda d: d.strftime("%d/%m/%Y"),
            key=f"lab_gamma_day_{week}",
        )
        kind = st.radio(
            "Scadenza di ricerca", ("0DTE", "Weekly"),
            horizontal=True, key=f"lab_gamma_expiry_{week}",
        )
        try:
            frames = merge_replay_sources(
                _study_frames(store, week, day),
                _official_frames(store, week, day, logs), day)
        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            st.warning(f"Archivio Replay per la ricerca non disponibile: {type(exc).__name__}.")
            return
        # Ordine effettivo dei download, non ordinamento alfabetico degli slot.
        available = sorted(
            ((slot, frame) for slot, frame in frames.items()
             if frame.get("profiles", {}).get(kind, {}).get("rows")
             and (kind == "Weekly" or not classify_0dte_download(
                 day, frame.get("download_rome"))["post_expiry"])),
            key=lambda pair: _download_clock(pair[1]) or
            datetime.max.replace(tzinfo=ROME),
        )
        render_gamma_research(available, kind, day, inside_expander=True)

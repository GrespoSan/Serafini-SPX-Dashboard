"""Replay temporaneo intraday: 3 snapshot ufficiali + replay intraday compatto.

Il grafico espone ORARI DI DOWNLOAD a Roma; il timestamp Cboe non viene
interpretato come tempo UTC o come ora italiana. Nessuna scrittura in Supabase.
"""
import json
import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from net_gamma import profile, read_snapshot
from feed_quality import summarize_timeline
from replay_scale import dynamic_gamma_bounds
from spx_market_clock import classify_0dte_download, cash_open_rome, spxw_expiry_close_rome

ROME = ZoneInfo("Europe/Rome")
SLOTS = ("07:30", "11:00", "13:30", "15:50", "16:01", "16:16",
         "16:31", "16:46", "17:30", "18:00", "18:30", "20:00", "21:30")
OFFICIAL = {"07:30": "30 7 * * 1-5", "13:30": "30 13 * * 1-5",
            "15:50": "50 15 * * 1-5"}

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


def _official_frames(store, week, day, logs):
    """Preferisci il primo evento valido nello slot ufficiale."""
    by_slot = {}
    for log in logs:
        try:
            item = json.loads(store.download(f"{week}/logs/{log['name']}"))
            slot = next((k for k, v in OFFICIAL.items()
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
                snap = read_snapshot(store.download(item["file"]))
                ref = float(snap["data"].get("prev_day_close") or 0)
                if ref <= 0:
                    continue
                profiles = {}
                for kind, expiry in (("0DTE", day), ("Weekly", weekly_expiry(day))):
                    calculated = profile(snap, expiry, ref)
                    if not calculated.get("rows"):
                        raise ValueError("Profilo vuoto")
                    profiles[kind] = {"rows": [
                        {"strike_spx": r["strike_spx"], "net_gamma": r["net_gamma"]}
                        for r in calculated["rows"]]}
                if not any(abs(r["net_gamma"]) > 0 for r in profiles["0DTE"]["rows"]):
                    continue
                result[slot] = {
                    "slot_rome": slot, "clock": clock.strftime("%H:%M:%S"),
                    "download_rome": clock.isoformat(timespec="seconds"),
                    "origin": "UFFICIALE", "spot": snap["data"].get("current_price"),
                    "reference_spx": ref, "profiles": profiles,
                    "cboe_timestamp_original": snap.get("timestamp"),
                }
                break
            except (KeyError, TypeError, ValueError, OSError, RuntimeError, OverflowError):
                continue
    return result


def _study_frames(store, week, day):
    data = {}
    for obj in store.list(f"replay/{week}/"):
        name = str(obj.get("name", ""))
        if not name.startswith(day.isoformat() + "_") or not name.endswith(".json"):
            continue
        try:
            item = json.loads(store.download(f"replay/{week}/{name}"))
            if (item.get("schema") != "serafini_replay_v1"
                    or item.get("rome_date") != day.isoformat()
                    or item.get("kind") != "STUDIO_INTRADAY_NON_UFFICIALE"):
                continue
            slot = item.get("slot_rome")
            if slot not in SLOTS or slot in OFFICIAL:
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


def render(store, week, logs, view_mode=None):
    st.subheader("Replay / History — evoluzione NET GAMMA")
    st.caption("Studio intraday, separato dal metodo ufficiale. "
               "I frame intermedi sono conservati per circa 30 giorni; "
               "dal processo Cloud. Nessun file originale Cboe aggiuntivo archiviato.")
    iso_year, iso_week = map(int, week.split("-W"))
    from datetime import timedelta
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    weekdays = [monday + timedelta(days=i) for i in range(5)]
    current_date = datetime.now(ROME).date()
    default_day = current_date if current_date in weekdays else weekdays[0]
    day = st.selectbox("Giornata replay", weekdays,
                       index=weekdays.index(default_day),
                       format_func=lambda d: d.strftime("%d/%m/%Y"),
                       key=f"replay_day_{week}")
    # Il Replay rimane dedicato al solo 0DTE.
    # Weekly resta disponibile nella sezione NET GAMMA 0DTE / Weekly.
    kind = "0DTE"
    st.caption(f"Orario USA per {day:%d/%m/%Y}: apertura 09:30 NY = "
               f"{cash_open_rome(day):%H:%M} Roma; "
               f"stop SPXW 0DTE 16:00 NY = "
               f"{spxw_expiry_close_rome(day):%H:%M} Roma. "
               "Cambio ora legale gestito automaticamente.")
    if view_mode is None:
        view_mode = st.radio("Visualizzazione", ("Solo NET GAMMA", "Candele ES + NET GAMMA"),
                             horizontal=True, key=f"replay_visual_{week}")
    elif view_mode not in ("Solo NET GAMMA", "Candele ES + NET GAMMA"):
        raise ValueError("Modalità Replay non valida")
    if st.button("Aggiorna replay", key=f"replay_reload_{week}"):
        st.rerun()
    with st.spinner("Caricamento profili compatti e snapshot ufficiali..."):
        frames = _study_frames(store, week, day)
        frames.update(_official_frames(store, week, day, logs))
    all_available = [(slot, frames[slot]) for slot in SLOTS if slot in frames
                     and frames[slot].get("profiles", {}).get(kind, {}).get("rows")]
    post_expiry = [
        (slot, frame) for slot, frame in all_available
        if classify_0dte_download(day, frame.get("download_rome"))["post_expiry"]
    ]
    # Esclusione solo dalla visualizzazione 0DTE e dalla GIF:
    # l'archivio e il report mantengono INTEGRO il dato post-scadenza.
    available = [(slot, frame) for slot, frame in all_available
                 if not classify_0dte_download(
                     day, frame.get("download_rome"))["post_expiry"]]
    missing = [slot for slot in SLOTS if slot not in frames]
    st.caption(f"Frame 0DTE pre-scadenza: {len(available)}/{len(SLOTS)} · "
               f"Assenti: {', '.join(missing) if missing else 'nessuno'}")
    if post_expiry:
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
    # Indicazione sobria: ripetizione del timestamp NON implica che l'intera
    # chain sia identica e timestamp diverso NON certifica la freschezza.
    feed_stats = summarize_timeline(available)
    st.caption(
        f"Timestamp Cboe rispetto al frame precedente: "
        f"{feed_stats['DIVERSO']} diversi · "
        f"{feed_stats['RIPETUTO']} ripetuti · "
        f"{feed_stats['NON VERIFICABILE']} non verificabili. "
        "Il primo rilevamento non ha un confronto; nessun fuso Cboe presunto."
    )
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


    offset_key = f"basis_override_{day.isoformat()}"
    proposed = str(st.session_state.get(offset_key, "")).strip()
    offset_text = st.text_input(
        "Differenziale ES − SPX (stesso valore per tutti i frame)",
        value=proposed, key=f"replay_basis_{week}_{day}",
        help="Verificare il differenziale ES−SPX in NET GAMMA. "
             "Vuoto = grafico in strike SPX.",
    )
    try:
        offset = float(offset_text.replace(",", ".")) if offset_text.strip() else None
    except ValueError:
        st.warning("Differenziale non numerico: mostro gli strike SPX.")
        offset = None
    if offset is None:
        st.warning("Il replay mostra strike SPX; per visualizzare livelli ES "
                   "inserire il differenziale corretto.")

    def numeric_spot(item):
        """Never substitute live prices for a historical Cboe observation."""
        try:
            spot = float(item.get("spot"))
            return spot if math.isfinite(spot) and spot > 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    all_rows = [r for _, f in available for r in f["profiles"][kind]["rows"]]
    strikes = sorted({float(r["strike_spx"]) for r in all_rows})
    # La scala non deve essere dominata dal massimo finale della giornata.
    # Ciascuno snapshot ha i propri limiti orizzontali; i valori restano gli stessi.
    def frame_bounds(item):
        return dynamic_gamma_bounds(
            [r["net_gamma"] for r in item["profiles"][kind]["rows"]])

    bounds = frame_bounds(available[0][1])

    # Axes remain numeric: a price line must represent its exact value even
    # when the price falls between two available option strikes.
    yvalues = [strike + (offset or 0) for strike in strikes]
    prices = [(numeric_spot(item) + (offset or 0))
              for _, item in available if numeric_spot(item) is not None]
    ybounds = [min(yvalues + prices) - 8, max(yvalues + prices) + 8]

    def frame_data(slot, item):
        vals = {float(r["strike_spx"]): float(r["net_gamma"])
                for r in item["profiles"][kind]["rows"]}
        xs = [vals.get(strike, 0.0) for strike in strikes]
        colors = ["#22c55e" if v >= 0 else "#ef4444" for v in xs]
        bars = go.Bar(
            x=xs, y=yvalues, orientation="h", width=3.6,
            marker_color=colors,
            hovertemplate="Livello %{y:,.2f}<br>NET Gamma %{x:+.3f}<extra></extra>",
            name=f"NET Gamma {slot}",
        )
        # Le etichette visualizzano la STESSA coordinata verticale delle barre:
        # prezzo ES (strike SPX + basis), oppure strike SPX se manca il basis.
        # Non si mostra il valore numerico NET GAMMA come etichetta:
        # rimane disponibile passando il mouse sulla barra.
        selected = [(strike, vals[strike]) for strike in strikes
                    if strike in vals and vals[strike] != 0]
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
        # Esattamente due tracce per frame: barre + etichette dei prezzi.
        # Compatibile con l'animazione e con le candele ES.
        return [bars, labels]

    def frame_title(slot, item):
        spot = numeric_spot(item)
        spot_text = f"SPX Cboe {spot:,.2f}" if spot is not None else "SPX non disponibile"
        es_text = (f" | ES stimato {spot + offset:,.2f}"
                   if spot is not None and offset is not None else "")
        cboe_time = str(item.get("cboe_timestamp_original") or "n.d.")
        return (f"NET GAMMA {kind} | {day:%d/%m/%Y} | "
                f"Slot {slot}, download {item['clock']} Roma"
                f"<br><sup>{spot_text}{es_text} | Timestamp Cboe: {cboe_time} "
                f"(fuso non dichiarato)</sup>")

    first_slot, first = available[0]
    graph = go.Figure(data=frame_data(first_slot, first))
    graph.frames = [
        go.Frame(name=slot, data=frame_data(slot, item), traces=[0, 1],
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
    if view_mode == "Candele ES + NET GAMMA":
        if offset is None:
            st.warning("Per affiancare le candele ES occorre il differenziale ES − SPX. "
                       "Senza differenziale i livelli Cboe sono strike SPX, non prezzi ES.")
        else:
            try:
                from replay_candles import combined_graph
                graph = combined_graph(graph, available, day, kind, offset, bounds, ybounds)
                st.caption("Sinistra: candele ES=F Yahoo 5m, soltanto barre chiuse entro l'ora "
                           "di download di ciascun frame. Destra: identico istogramma NET GAMMA "
                           "Cboe. Le due fonti possono differire; "
                           "i dati 5m Yahoo hanno uno storico limitato.")
            except Exception as exc:
                st.warning(f"Candele ES non disponibili ({exc}). Resta visibile il Replay NET GAMMA originale.")
    st.plotly_chart(graph, use_container_width=True,
                    key=f"replay_chart_{week}_{day}_{kind}_{view_mode}")

    # GIF solo su richiesta: nessuna generazione nei normali rerun del replay.
    # Conserviamo una sola GIF in memoria per sessione e invalidiamo la cache
    # se cambiano giorno, scadenza, differenziale o gli snapshot disponibili.
    gif_signature = ("dynamic_gamma_scale_v3_es_labels", day.isoformat(), kind, offset,
                     tuple((slot, item.get("clock"),
                            tuple((float(x["strike_spx"]), float(x["net_gamma"]))
                                  for x in item["profiles"][kind]["rows"]))
                           for slot, item in available))
    gif_key = "replay_gif_last"
    if st.button("Esporta GIF giornata", key=f"gif_prepare_{week}_{day}_{kind}",
                 disabled=offset is None or len(available) < 2,
                 help="Genera solo su richiesta la GIF di tutti i rilevamenti "
                      "disponibili, con le candele ES fino a ciascun download."):
        try:
            from replay_gif import generate_gif
            with st.spinner("Creazione GIF dai rilevamenti disponibili..."):
                gif_data, gif_frames = generate_gif(available, day, kind, offset)
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
    st.caption("GIF manuale 0DTE: scala NET GAMMA dinamica per rilevamento, "
               "nessuna interpolazione dei profili. Le candele ES includono "
               "solo barre chiuse al momento del download.")
    st.caption("Il prezzo SPX Cboe e l'eventuale ES stimato rimangono nel titolo "
               "e nella tabella dei rilevamenti; non sono tracciati come linea "
               "sull'istogramma. Il timestamp Cboe non dichiara il fuso orario.")

    st.caption("Scala NET GAMMA dinamica per rilevamento: le lunghezze delle barre "
               "fra frame NON sono direttamente confrontabili; usare i valori "
               "numerici e l'asse corrente. Barre verdi: NET positivo; "
               "rosse: NET negativo. Le etichette accanto all'asse zero mostrano "
               "il PREZZO ES se il differenziale ES−SPX è disponibile; "
               "altrimenti indicano lo strike SPX. Etichette delle barre verdi "
               "a sinistra e delle rosse a destra. Il NET GAMMA è disponibile "
               "al passaggio del mouse.")
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
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.caption("I tre frame ufficiali provengono dall'archivio invariato. "
               "I frame intermedi NON entrano nei report ufficiali. "
               "L'orario di download non garantisce la freschezza della chain Cboe.")

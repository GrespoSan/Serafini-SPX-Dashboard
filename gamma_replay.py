"""Replay temporaneo intraday: 2 snapshot ufficiali + 7 frame compatti di studio.

Il grafico espone ORARI DI DOWNLOAD a Roma; il timestamp Cboe non viene
interpretato come tempo UTC o come ora italiana. Nessuna scrittura in Supabase.
"""
import json
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from net_gamma import profile, read_snapshot

ROME = ZoneInfo("Europe/Rome")
SLOTS = ("07:30", "11:00", "13:30", "15:46", "16:01", "16:16",
         "16:31", "16:46", "17:30")
OFFICIAL = {"07:30": "30 7 * * 1-5", "15:46": "46 15 * * 1-5"}

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
                "origin": "STUDIO NON UFFICIALE",
                "spot": item.get("spot_spx"),
                "reference_spx": item.get("reference_spx"),
                "cboe_timestamp_original": item.get("cboe_timestamp_original"),
                "profiles": profiles,
            }
        except (KeyError, ValueError, TypeError, RuntimeError, OverflowError):
            continue
    return data


def render(store, week, logs):
    st.subheader("Replay / History — evoluzione NET GAMMA")
    st.caption("Studio intraday, separato dal metodo ufficiale. "
               "I frame intermedi sono temporanei; vengono rimossi dopo circa 2 giorni "
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
    kind = st.radio("Scadenza", ("0DTE", "Weekly"), horizontal=True,
                    key=f"replay_expiry_{week}")
    if st.button("Aggiorna replay", key=f"replay_reload_{week}"):
        st.rerun()
    with st.spinner("Caricamento profili compatti e snapshot ufficiali..."):
        frames = _study_frames(store, week, day)
        frames.update(_official_frames(store, week, day, logs))
    available = [(slot, frames[slot]) for slot in SLOTS if slot in frames
                 and frames[slot].get("profiles", {}).get(kind, {}).get("rows")]
    missing = [s for s in SLOTS if s not in frames]
    st.caption(f"Frame disponibili: {len(available)}/9 · "
               f"Assenti: {', '.join(missing) if missing else 'nessuno'}")
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
    xmin = min(float(r["net_gamma"]) for r in all_rows)
    xmax = max(float(r["net_gamma"]) for r in all_rows)
    spread = max(xmax - xmin, 1.0)
    bounds = [min(0, xmin) - spread * 0.08, max(0, xmax) + spread * 0.08]

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
        spot = numeric_spot(item)
        if spot is not None:
            estimated = spot + (offset or 0)
            price_note = (f"ES stimato {estimated:,.2f}" if offset is not None
                          else f"SPX Cboe {spot:,.2f}")
            marker = go.Scatter(
                x=[bounds[0], bounds[1]], y=[estimated, estimated],
                mode="lines", line={"color": "#fbbf24", "width": 2.5, "dash": "dash"},
                name=price_note, showlegend=False,
                hovertemplate=price_note + "<extra></extra>",
            )
        else:
            marker = go.Scatter(
                x=[], y=[], mode="lines", showlegend=False,
                name="Prezzo Cboe non disponibile",
            )
        return [bars, marker]

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
                 layout=go.Layout(title={"text": frame_title(slot, item)}))
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
    st.plotly_chart(graph, use_container_width=True,
                    key=f"replay_chart_{week}_{day}_{kind}")
    st.caption("Linea gialla tratteggiata: prezzo SPX rilevato da Cboe, "
               "traslato sul future ES con il differenziale indicato (se presente). "
               "Prezzo potenzialmente ritardato; ogni frame usa il proprio prezzo "
               "storico, mai la quotazione al momento del replay. "
               "Orario Cboe senza fuso dichiarato.")

    st.caption("Scala NET GAMMA fissa per tutti i frame. Barre verdi: NET positivo; "
               "rosse: NET negativo. Lo spostamento apparente puo' dipendere "
               "sia da variazioni delle posizioni sia della Gamma delle opzioni.")
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
    st.caption("I due frame ufficiali provengono dall'archivio invariato. "
               "I sette frame intermedi NON entrano nei report ufficiali. "
               "L'orario di download non garantisce la freschezza della chain Cboe.")

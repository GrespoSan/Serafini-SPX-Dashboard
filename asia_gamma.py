"""Sessione asiatica ES 00:00–09:00 Roma; indipendente dai calcoli NET GAMMA."""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import math
import pandas as pd
import streamlit as st

ROME = ZoneInfo("Europe/Rome")


def _column(frame, field):
    """Seleziona solo la colonna ES=F anche con MultiIndex Yahoo."""
    if isinstance(frame.columns, pd.MultiIndex):
        for key in ((field, "ES=F"), ("ES=F", field)):
            if key in frame.columns:
                return frame[key]
        raise ValueError(f"Colonna {field} ES=F non riconosciuta.")
    if field not in frame.columns:
        raise ValueError(f"Colonna {field} mancante.")
    return frame[field]


@st.cache_data(ttl=120, max_entries=90, show_spinner=False)
def asian_session(target: date):
    """Barre 5m [00:00,09:00) Roma; verifica integrita' e OHLC."""
    import yfinance as yf

    now = datetime.now(ROME)
    if target > now.date() or (target == now.date() and now.time() < time(9, 0)):
        raise ValueError("La sessione asiatica non è ancora terminata alle 09:00.")
    if target < now.date() - timedelta(days=55):
        raise ValueError("Storico ES a 5 minuti non disponibile per questa data.")
    frame = yf.download(
        "ES=F", start=(target - timedelta(days=1)).isoformat(),
        end=(target + timedelta(days=1)).isoformat(),
        interval="5m", auto_adjust=False, progress=False,
        threads=False, prepost=True,
    )
    if frame is None or frame.empty or frame.index.tz is None:
        raise ValueError("Yahoo non ha restituito barre intraday ES valide.")
    frame = frame.copy()
    frame.index = frame.index.tz_convert(ROME)
    start = datetime.combine(target, time(0, 0), ROME)
    end = datetime.combine(target, time(9, 0), ROME)
    expected = pd.date_range(start=start, end=end, freq="5min", inclusive="left")
    bars = frame[(frame.index >= start) & (frame.index < end)].copy()
    if bars.index.has_duplicates:
        raise ValueError("Timestamp ES duplicati nella sessione asiatica.")
    missing = expected.difference(bars.index)
    extra = bars.index.difference(expected)
    # Fino a 3 lacune: rendi gli estremi PROVVISORI, non bloccare anche la chiusura.
    if len(extra) or len(missing) > 3 or len(bars) < len(expected) - 3:
        raise ValueError(
            f"Sessione incompleta: {len(bars)}/{len(expected)} barre; "
            f"{len(missing)} mancanti, {len(extra)} fuori griglia."
        )
    if expected[-1] not in bars.index:
        raise ValueError("Manca la barra conclusiva 08:55–09:00: chiusura non verificabile.")
    # Le barre mancanti non consentono di certificare gli estremi di tutta la sessione.
    high = pd.to_numeric(_column(bars, "High"), errors="coerce")
    low = pd.to_numeric(_column(bars, "Low"), errors="coerce")
    close = pd.to_numeric(_column(bars, "Close"), errors="coerce")
    if not (high.notna().all() and low.notna().all() and close.notna().all()):
        raise ValueError("Barre OHLC con valori mancanti.")
    if not all(math.isfinite(float(v)) and float(v) > 0
               for col in (high, low, close) for v in col):
        raise ValueError("Barre OHLC con valori non validi.")
    if (high < low).any() or (close > high).any() or (close < low).any():
        raise ValueError("Barre OHLC incoerenti.")
    hi, lo, last = float(high.max()), float(low.min()), float(close.iloc[-1])
    return {
        "high": hi, "low": lo, "close": last,
        "bars": len(bars), "expected": len(expected),
        "missing_times": [t.strftime("%H:%M") for t in missing],
        "timeline": [{"time": t.strftime("%H:%M"), "high": float(high.loc[t]),
                      "low": float(low.loc[t]), "close": float(close.loc[t])}
                     for t in bars.index],
        "source": "Yahoo Finance ES=F · barre 5 minuti, 00:00–09:00 Roma",
    }


def close_at_nine(target: date):
    """Compatibilita' con la versione precedente."""
    result = asian_session(target)
    return {"price": result["close"], "source": result["source"],
            "time": target.isoformat() + " 09:00"}



def level_events(result, level, name):
    """Transizioni sulle CHIUSURE 5m, senza inventare ordine degli spike intrabar."""
    events = []
    rows = result["timeline"]
    for prev, current in zip(rows, rows[1:]):
        prev_time = datetime.strptime(prev["time"], "%H:%M")
        cur_time = datetime.strptime(current["time"], "%H:%M")
        if (cur_time - prev_time).total_seconds() != 300:
            continue  # Una lacuna impedisce di localizzare l'attraversamento.
        a, b = prev["close"] - level, current["close"] - level
        clock = (cur_time + timedelta(minutes=5)).strftime("%H:%M")
        if a <= 0 < b:
            events.append(f"{name} superato al rialzo: chiusura {clock}")
        elif a >= 0 > b:
            events.append(f"{name} perso al ribasso: chiusura {clock}")
    touched = any(row["low"] <= level <= row["high"] for row in rows)
    recovered = rows[-1]["close"] > level
    return events, touched, recovered


def render(target: date, c1_es, p1_es):
    st.markdown("**Sessione asiatica — 00:00–09:00 (Roma)**")
    try:
        result = asian_session(target)
    except Exception as exc:
        st.info(f"Dati sessione asiatica non disponibili: {exc}")
        return

    hi, lo, price = result["high"], result["low"], result["close"]
    a, b, c = st.columns(3)
    a.metric("Massimo Asia", f"{hi:,.2f}")
    b.metric("Minimo Asia", f"{lo:,.2f}")
    c.metric("Chiusura ore 09:00", f"{price:,.2f}")
    if result["missing_times"]:
        missing_count = len(result["missing_times"])
        description = ("manca la barra delle " if missing_count == 1
                       else "mancano le barre delle ")
        message = ("Massimo e minimo PROVVISORI: " + description +
                   ", ".join(result["missing_times"]) +
                   f" ({result['bars']}/{result['expected']} barre). "
                   "Chiusura delle 09:00 verificata sulla barra finale; "
                   "gli estremi potrebbero cambiare.")
        st.markdown(
            f'<span style="color:#ef4444;font-size:0.88rem">{message}</span>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"{result['source']} · {result['bars']}/{result['expected']} barre verificate.")

    if c1_es is None or p1_es is None or not p1_es < c1_es:
        st.info("Per classificare la posizione, seleziona C1 e P1 validi "
                "e indica il differenziale ES − SPX.")
        return

    st.markdown(f"**Riferimenti NET GAMMA (ES): C1 {c1_es:,.2f} · P1 {p1_es:,.2f}**")
    if price > c1_es:
        zone = "Sopra C1 — zona superiore"
    elif price < p1_es:
        zone = "Sotto P1 — zona inferiore"
    else:
        zone = "Tra P1 e C1 — fascia intermedia (Gamma Flip non calcolato)"

    if hi < p1_es:
        behavior = "Intera sessione sotto P1"
    elif lo > c1_es:
        behavior = "Intera sessione sopra C1"
    elif lo > p1_es and hi < c1_es:
        behavior = "Intera sessione fra P1 e C1"
    elif lo < p1_es and hi > c1_es:
        behavior = "La sessione ha attraversato sia P1 sia C1"
    elif lo < p1_es:
        behavior = "La sessione ha attraversato P1"
    elif hi > c1_es:
        behavior = "La sessione ha attraversato C1"
    else:
        behavior = "La sessione ha toccato uno o entrambi i livelli"

    st.markdown(f"**Posizione alle 09:00:** {zone}")
    st.markdown(f"**Andamento della sessione:** {behavior}" +
                (" (provvisorio: barra mancante)" if result["missing_times"] else ""))
    p1_events, _, _ = level_events(result, p1_es, "P1")
    c1_events, _, _ = level_events(result, c1_es, "C1")
    if price < p1_es:
        verdict = "CHIUSURA ASIATICA SOTTO P1 — ZONA INFERIORE"
        distance = f"{p1_es - price:,.2f} punti sotto P1"
        relevant = p1_events
    elif price > c1_es:
        verdict = "CHIUSURA ASIATICA SOPRA C1 — ZONA SUPERIORE"
        distance = f"{price - c1_es:,.2f} punti sopra C1"
        relevant = c1_events
    else:
        verdict = "CHIUSURA ASIATICA FRA P1 E C1 — FASCIA INTERMEDIA / FLIP NON VERIFICATO"
        distance = f"{price - p1_es:,.2f} punti sopra P1; {c1_es - price:,.2f} punti sotto C1"
        relevant = p1_events + c1_events
    if price < p1_es:
        st.markdown(
            f'### Verdetto ore 09:00: <span style="color:#ef4444">{verdict}</span>',
            unsafe_allow_html=True,
        )
    elif price > c1_es:
        st.markdown(
            f'### Verdetto ore 09:00: <span style="color:#22c55e">{verdict}</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'### Verdetto ore 09:00: <span style="color:#f59e0b">{verdict}</span>',
            unsafe_allow_html=True,
        )
    st.write(distance + ".")
    if p1_es <= price <= c1_es:
        if lo < p1_es and hi < c1_es:
            st.markdown("**Lettura indicativa:** incerta, con elementi di debolezza: "
                        "il minimo disponibile è sotto P1, mentre il massimo "
                        "disponibile non raggiunge C1. "
                        "La chiusura ha comunque recuperato P1. "
                        "Non è una misura del Gamma Flip né un segnale short.")
        else:
            st.caption("Tra P1 e C1 si identifica soltanto una fascia di prezzo, "
                       "non il Gamma Flip matematico dei dealer.")
        if result["missing_times"]:
            st.caption("Massimo/minimo provvisori: le barre assenti possono "
                       "modificare la lettura del percorso, non la chiusura delle 09:00.")
    if relevant:
        st.caption("Ultimo attraversamento confermato (chiusure 5m): " +
                   relevant[-1] + ".")
    if result["missing_times"]:
        st.caption("La posizione delle 09:00 è confermata dalla barra finale; "
                   "la cronologia può essere incompleta per la barra mancante.")
    with st.expander("Cronologia attraversamenti C1 / P1 (chiusure 5 minuti)", expanded=False):
        for name, level in (("P1", p1_es), ("C1", c1_es)):
            events, touched, recovered = level_events(result, level, name)
            st.markdown(f"**{name} — {level:,.2f} ES**")
            if events:
                for event in events:
                    st.write("• " + event)
            else:
                st.caption("Nessun attraversamento confermato dalle chiusure consecutive a 5 minuti.")
            if touched and not events:
                st.caption("Livello toccato dalle oscillazioni High/Low, ma senza attraversamento "
                           "confermato dalle chiusure consecutive.")
            st.caption("Alle 09:00: " + ("sopra il livello (recuperato/tenuto)" if recovered
                                       else "al livello o sotto il livello") +
                       ". Le transizioni vicine a eventuali barre mancanti non sono verificabili.")
    st.caption("Confronto con candidati C1/P1 sperimentali: "
               "non è una misura diretta del gamma dei dealer.")



def render_asia_context(target: date, profile_0dte, profile_weekly, offset):
    """Evidenzia vicinanze fra estremi Asia e barre 0DTE; NON assegna C/P."""
    if offset is None or not profile_0dte or not profile_0dte.get("rows"):
        return
    try:
        result = asian_session(target)
    except Exception as exc:
        st.caption(f"Confluenze Asia/NET GAMMA non disponibili: {exc}")
        return
    weekly_rows = (profile_weekly or {}).get("rows") or []
    weekly_by_strike = {float(r["strike_spx"]): float(r["net_gamma"])
                        for r in weekly_rows}
    data = []
    # 15 punti e' SOLO una finestra di esplorazione, non un criterio del docente.
    for edge_name, value in (("Massimo Asia", result["high"]),
                             ("Minimo Asia", result["low"])):
        candidates = []
        for item in profile_0dte["rows"]:
            strike = float(item["strike_spx"])
            level = strike + float(offset)
            distance = level - value
            if abs(distance) > 15.0:
                continue
            net = float(item["net_gamma"])
            candidates.append((abs(distance), -abs(net), strike, level, net, distance))
        candidates.sort()
        for _, _, strike, level, net, distance in candidates[:5]:
            weekly = weekly_by_strike.get(strike)
            data.append({
                "Estremo": edge_name + (" (provv.)" if result["missing_times"] else ""),
                "Livello ES": round(level, 2),
                "Scarto ES": round(distance, 2),
                "NET 0DTE": round(net, 3),
                "Segno 0DTE": ("Positivo" if net > 0 else "Negativo" if net < 0 else "Zero"),
                "NET Weekly": (round(weekly, 3) if weekly is not None else None),
            })
    st.markdown("**Concentrazioni NET GAMMA vicine al range Asia (studio sperimentale)**")
    if data:
        st.dataframe(data, use_container_width=True, hide_index=True)
    else:
        st.caption("Nessuno strike 0DTE entro 15 punti dagli estremi disponibili.")
    st.caption("Mostrati fino a 5 strike 0DTE per estremo, a distanza massima 15 punti ES; "
               "lo scarto è livello meno estremo (segno positivo = sopra). "
               "Weekly riporta lo stesso strike, non una conferma automatica. "
               "La prossimità NON prova concentrazione significativa, Gamma Flip o ruolo C/P. "
               "La soglia di 15 punti è un filtro di esplorazione, non una regola attribuita a Serafini.")


def render_auto(target: date, c1_es, p1_es):
    """Aggiorna soltanto il riquadro Asia, senza F5 o ricalcolo NET GAMMA."""
    now = datetime.now(ROME)
    # Solo la giornata odierna, dalla prima apertura della pagina fino alle 09:45.
    live = target == now.date() and now.time() < time(9, 45)
    if not live:
        render(target, c1_es, p1_es)
        return

    @st.fragment(run_every="2min")
    def _asian_live_fragment():
        current = datetime.now(ROME)
        if current.time() >= time(9, 45):
            st.rerun()  # una sola esecuzione completa per interrompere il timer
        render(target, c1_es, p1_es)
        st.caption("Sessione asiatica: aggiornamento automatico ogni 2 minuti "
                   "fino alle 09:45, senza ricaricare l'app.")
    _asian_live_fragment()

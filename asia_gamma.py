"""Contesto ES della sessione asiatica, indipendente dal calcolo NET GAMMA."""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import math
import streamlit as st

ROME = ZoneInfo("Europe/Rome")


@st.cache_data(ttl=900, max_entries=90, show_spinner=False)
def close_at_nine(target: date):
    """Ultima candela ES=F 5m 08:55-09:00 Roma, senza dati successivi alle 09."""
    import yfinance as yf
    now = datetime.now(ROME)
    if target > now.date() or (target == now.date() and now.time() < time(9, 0)):
        raise ValueError("La sessione asiatica non è ancora terminata alle 09:00.")
    if target < now.date() - timedelta(days=55):
        raise ValueError("Storico ES a 5 minuti non disponibile gratuitamente per questa data.")
    frame = yf.download("ES=F", start=(target - timedelta(days=1)).isoformat(),
                        end=(target + timedelta(days=1)).isoformat(),
                        interval="5m", auto_adjust=False, progress=False, threads=False,
                        prepost=True)
    if frame is None or frame.empty or frame.index.tz is None:
        raise ValueError("Yahoo non ha restituito barre intraday ES valide.")
    frame.index = frame.index.tz_convert(ROME)
    bars = frame[(frame.index.date == target) &
                 (frame.index.hour == 8) & (frame.index.minute == 55)]
    if len(bars) != 1:
        raise ValueError("Barra ES 08:55–09:00 Roma assente: nessuna chiusura stimata.")
    columns = frame.columns
    if hasattr(columns, "nlevels") and columns.nlevels > 1:
        if ("Close", "ES=F") in columns:
            value = bars[("Close", "ES=F")].iloc[-1]
        elif ("ES=F", "Close") in columns:
            value = bars[("ES=F", "Close")].iloc[-1]
        else:
            raise ValueError("Colonna Close ES non identificabile.")
    else:
        value = bars["Close"].iloc[-1]
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Chiusura ES non valida.")
    return {"price": value, "source": "Yahoo Finance ES=F · barra 08:55–09:00 (Roma)",
            "time": target.isoformat() + " 09:00"}


def render(target: date, c1_es, p1_es):
    st.markdown("**Sessione asiatica — posizione ES alle 09:00 (Roma)**")
    if c1_es is None or p1_es is None or not p1_es < c1_es:
        st.info("Seleziona C1 e P1 validi e indica il differenziale ES − SPX.")
        return
    try:
        result = close_at_nine(target)
    except Exception as exc:
        st.info(f"Chiusura asiatica non disponibile: {exc}")
        return
    price = result["price"]
    if price > c1_es:
        zone = "Sopra C1 — zona superiore"
    elif price < p1_es:
        zone = "Sotto P1 — zona inferiore"
    else:
        zone = "Tra P1 e C1 — area flip operativa"
    a, b, c = st.columns(3)
    a.metric("ES ore 09:00", f"{price:,.2f}")
    b.metric("C1 (ES)", f"{c1_es:,.2f}")
    c.metric("P1 (ES)", f"{p1_es:,.2f}")
    st.markdown(f"**Posizione alle 09:00: {zone}**")
    st.caption(result["source"] +
               ". Classificazione di posizione rispetto ai livelli selezionati, "
               "non misura diretta del Gamma Flip dei dealer. I livelli C/P restano sperimentali.")

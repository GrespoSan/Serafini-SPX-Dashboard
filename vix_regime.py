"""Regime VIX da settlement Cboe del trading day precedente.

Si usano SOLO contratti VX mensili STANDARD, senza scadenze weekly.
La percentuale e' (M2/M1 - 1)*100; le opzioni Cboe SPX restano intatte.
"""
import csv
import io
import re
from datetime import date, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

CSV_URL = "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv"
SOURCE_PAGE = "https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily"
MONTHLY = re.compile(r"(?<![A-Z0-9])VX/[FGHJKMNQUVXZ][0-9]{1,2}(?![A-Z0-9])")
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2}/20\d\d)\b")
DECIMAL = re.compile(r"(?<![A-Za-z0-9])\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![A-Za-z0-9])")


def _parse_settlements(text, settlement_day):
    """Estrae expiry e settlement dalle righe VX mensili del CSV Cboe.

    Filtro intenzionalmente restrittivo: VX43/X6 e VXM/X6 non sono
    i futures mensili VX/X6. Non usa date di scadenza gia' trascorse.
    """
    values = {}
    for cells in csv.reader(io.StringIO(text)):
        joined = " | ".join(c.strip() for c in cells)
        symbol = MONTHLY.search(joined)
        if not symbol:
            continue
        tail = joined[symbol.end():]
        found = DATE.search(tail)
        if not found:
            continue
        raw_day = found.group(1)
        try:
            expiry = (date.fromisoformat(raw_day) if "-" in raw_day
                      else date(int(raw_day.split("/")[2]), int(raw_day.split("/")[0]), int(raw_day.split("/")[1])))
        except ValueError:
            continue
        if expiry <= settlement_day:
            continue
        after_date = tail[found.end():]
        # Il primo numero DOPO la data di scadenza e' il prezzo settlement.
        val = DECIMAL.search(after_date)
        if not val:
            continue
        try:
            settlement = float(val.group().replace(",", ""))
        except ValueError:
            continue
        if not 0 < settlement < 200:
            continue
        values[(symbol.group(), expiry)] = settlement
    ordered = sorted(((expiry, symbol, price)
                      for (symbol, expiry), price in values.items()), key=lambda x:x[0])
    if len(ordered) < 2:
        raise ValueError("Cboe: impossibile identificare due futures VX mensili validi nel CSV")
    front, second = ordered[:2]
    return {
        "m1_symbol": front[1], "m1_expiry": front[0].isoformat(), "m1": front[2],
        "m2_symbol": second[1], "m2_expiry": second[0].isoformat(), "m2": second[2],
        "contango_pct": (second[2] / front[2] - 1) * 100,
    }


@st.cache_data(show_spinner=False, ttl=21600, max_entries=80)
def _download_settlement(trading_day):
    url = CSV_URL + "?" + urlencode({"dt": trading_day.isoformat()})
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,text/plain,*/*",
    })
    with urlopen(request, timeout=12) as response:
        raw = response.read(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError("CSV Cboe troppo grande")
    text = raw.decode("utf-8-sig", errors="replace")
    result = _parse_settlements(text, trading_day)
    result["day"] = trading_day.isoformat()
    return result


@st.cache_data(show_spinner=False, ttl=21600, max_entries=70)
def previous_vix_curve(target_day):
    """Legge la PRIMA seduta valida PRIMA di target_day, mai dati odierni."""
    errors = []
    for lag in range(1, 8):
        candidate = target_day - timedelta(days=lag)
        if candidate.weekday() >= 5:
            continue
        try:
            return _download_settlement(candidate)
        except Exception as exc:
            errors.append(f"{candidate.isoformat()} ({type(exc).__name__})")
    raise ValueError("Nessun settlement VX mensile Cboe confermato nei 7 giorni precedenti: "
                     + ", ".join(errors))


def render(target_day):
    """Mostra regime e buffer affiancati, senza modificare NET GAMMA."""
    try:
        auto = previous_vix_curve(target_day)
        pct = auto["contango_pct"]
    except Exception:
        pct = None

    if pct is None:
        regime, buffer = "Non disponibile", "Non determinato"
    elif pct < 0:
        regime, buffer = f"Backwardation {pct:+.2f}%", "15 punti"
    elif pct < 1.5:
        regime, buffer = f"Contango {pct:+.2f}%", "15 punti"
    else:
        regime, buffer = f"Contango {pct:+.2f}%", "10 punti"

    col_regime, col_buffer = st.columns(2, gap="medium")
    with col_regime:
        st.markdown(f"**Regime VIX:** {regime}")
    with col_buffer:
        st.markdown(f"**Buffer breakout:** {buffer}")
    return pct

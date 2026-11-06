"""Official Cboe VIX spot close for the completed preceding U.S. session.

VIX index is NOT VX futures. This value is DISPLAY ONLY and never feeds
the Serafini VX/ES buffer formula or changes NET GAMMA.
"""
import csv
import io
import math
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen

import streamlit as st

VIX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
VIX_SOURCE_PAGE = "https://www.cboe.com/tradable_products/vix/vix_historical_data"


def _parse_close_history(raw_csv, target_day, expected_day=None):
    """Last valid published VIX close strictly before target_day.

    If Massive ES verified the prior session, require the same date:
    no mixing yesterday's ES with an older VIX close on holidays/outages.
    """
    if not isinstance(target_day, date) or isinstance(target_day, datetime):
        raise ValueError("Data VIX di riferimento non valida.")
    if expected_day is not None:
        if isinstance(expected_day, str):
            try:
                expected_day = date.fromisoformat(expected_day)
            except ValueError as exc:
                raise ValueError("Data della seduta ES non valida.") from exc
        if not isinstance(expected_day, date) or isinstance(expected_day, datetime):
            raise ValueError("Data della seduta ES non valida.")
        if expected_day >= target_day:
            raise ValueError("La chiusura VIX deve precedere la giornata di riferimento.")
    if isinstance(raw_csv, bytes):
        text = raw_csv.decode("utf-8-sig", errors="replace")
    elif isinstance(raw_csv, str):
        text = raw_csv.lstrip("\ufeff")
    else:
        raise ValueError("Storico VIX in formato non valido.")
    # Cboe CSV may be preceded by a descriptive header line.
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if _is_csv_header(line)), None)
    if start is None:
        raise ValueError("Colonne DATE/CLOSE non disponibili nello storico Cboe.")
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    names = {str(k).strip().upper():k for k in (reader.fieldnames or [])}
    if "DATE" not in names or "CLOSE" not in names:
        raise ValueError("Colonne DATE/CLOSE non disponibili nello storico Cboe.")
    found = {}
    for row in reader:
        raw_day = str(row.get(names["DATE"], "") or "").strip()
        raw_price = str(row.get(names["CLOSE"], "") or "").strip()
        try:
            day = next(datetime.strptime(raw_day, pattern).date()
                       for pattern in ("%m/%d/%Y", "%Y-%m-%d")
                       if _valid_date(raw_day, pattern))
            price = float(raw_price.replace(",", ""))
        except (StopIteration, ValueError, TypeError):
            continue
        if day >= target_day or (target_day - day) > timedelta(days=7):
            continue
        if not (math.isfinite(price) and 0 < price < 200):
            continue
        found[day] = price
    if not found:
        raise ValueError("Nessuna chiusura VIX recente antecedente al giorno richiesto.")
    chosen = expected_day if expected_day is not None else max(found)
    if chosen not in found:
        raise ValueError("Chiusura VIX non presente per la seduta ES precedente.")
    return {"status":"VALIDO", "day":chosen.isoformat(),
            "close":found[chosen], "source":"Cboe · VIX Index storico · CLOSE"}


def _is_csv_header(text):
    try:
        columns = next(csv.reader([text.strip().lstrip("\ufeff")]))
        normalized = [c.strip().upper() for c in columns]
        return "DATE" in normalized and "CLOSE" in normalized
    except (StopIteration, csv.Error):
        return False


def _valid_date(text, pattern):
    try:
        datetime.strptime(text, pattern)
        return True
    except ValueError:
        return False


@st.cache_data(ttl=3600, max_entries=3, show_spinner=False)
def _download_vix_history():
    request = Request(VIX_HISTORY_URL, headers={
        "User-Agent":"Mozilla/5.0", "Accept":"text/csv,text/plain,*/*"})
    with urlopen(request, timeout=10) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000 or not raw:
        raise ValueError("Risposta storico VIX vuota o troppo grande.")
    return raw


def previous_vix_spot(target_day, expected_es_day=None):
    """Read-only VIX close. Failure raises to caller for neutral N/D UI."""
    return _parse_close_history(_download_vix_history(), target_day, expected_es_day)

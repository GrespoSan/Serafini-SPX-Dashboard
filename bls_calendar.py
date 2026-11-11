"""Calendario BLS indipendente per CPI, PPI, NFP e JOLTS.

Solo orari di pubblicazione PREVISTI; non sono annunci di dati realmente
pubblicati. Feed iCalendar BLS; fallback alla tabella mensile BLS.
Nessuna API key. Qualsiasi indisponibilità viene propagata al chiamante.
"""
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import streamlit as st

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")
ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
MONTH_URL = "https://www.bls.gov/schedule/{year}/{month:02d}_sched_list.htm"

BLS_TYPES = {
    "CPI": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "PPI": "https://www.bls.gov/schedule/news_release/ppi.htm",
    "NFP": "https://www.bls.gov/schedule/news_release/empsit.htm",
    "JOLTS": "https://www.bls.gov/schedule/news_release/jolts.htm",
}


def _kind(title):
    text = " ".join(str(title or "").lower().split())
    if "consumer price index" in text:
        return "CPI"
    if "producer price index" in text:
        return "PPI"
    if "employment situation" in text:
        return "NFP"
    if "job openings and labor turnover" in text:
        return "JOLTS"
    return None


def _read_url(url, *, accept):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SerafiniSPXResearch/1.0)",
        "Accept": accept,
    })
    with urlopen(request, timeout=12) as response:
        payload = response.read(1_800_001)
    if len(payload) > 1_800_000:
        raise ValueError("Risposta BLS troppo grande")
    return payload.decode("utf-8-sig", errors="replace")


def _unfold(text):
    """RFC 5545: linee che iniziano con spazio/tab continuano la precedente."""
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_dt(key, value):
    """Accetta solo istanti con timezone o gli orari ET espliciti."""
    v = value.strip()
    if "VALUE=DATE" in key.upper() or re.fullmatch(r"\d{8}", v):
        return None
    match = re.fullmatch(r"(\d{8})T(\d{4})(\d{2})?(Z)?", v)
    if not match:
        return None
    stamp, hhmm, seconds, z = match.groups()
    parsed = datetime.strptime(stamp + hhmm + (seconds or "00"), "%Y%m%d%H%M%S")
    if z:
        return parsed.replace(tzinfo=timezone.utc)
    zone = re.search(r"TZID=([^;:]+)", key, flags=re.I)
    if zone:
        try:
            return parsed.replace(tzinfo=ZoneInfo(zone.group(1)))
        except KeyError:
            return None
    # Assenza di TZID/Z: non assumere un fuso orario.
    return None


def _event(daytime, kind, source=BLS_TYPES):
    when = daytime.astimezone(ROME)
    return {
        "day": when.date(), "clock": when.strftime("%H:%M"),
        "kind": kind, "priority": "ALTA", "status": "scheduled",
        "source": source[kind], "provider": "BLS",
        "official_schedule": True,
    }


def parse_ics(text, first_day, last_day):
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValueError("Calendario BLS ICS non riconosciuto")
    rows, entry = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            entry = {}
        elif line == "END:VEVENT":
            if entry:
                kind = _kind(entry.get("SUMMARY", ""))
                datepair = entry.get("DTSTART")
                if kind and datepair:
                    dt = _ics_dt(*datepair)
                    if dt is not None:
                        event = _event(dt, kind)
                        if first_day <= event["day"] <= last_day:
                            rows.append(event)
            entry = None
        elif entry is not None and ":" in line:
            key, value = line.split(":", 1)
            root = key.split(";", 1)[0].upper()
            if root == "DTSTART":
                entry[root] = (key, value)
            elif root == "SUMMARY":
                entry[root] = value.replace("\\,", ",").replace("\\n", " ")
    return _unique(rows)


class _BlsRows(HTMLParser):
    """Legge righe tabellari senza regex HTML e senza dipendenze esterne."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current_row = None
        self.cell = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.cell = []
            self.depth = 1
        elif self.cell is not None:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.current_row.append(" ".join("".join(self.cell).split()))
            self.cell = None
            self.depth = 0
        elif tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None
        elif self.cell is not None:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def parse_month_html(text, first_day, last_day):
    parser = _BlsRows()
    parser.feed(text)
    events = []
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        match = re.search(
            r"(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?"
            r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", cells[0])
        if not match:
            continue
        try:
            d = datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
            time_text = cells[1].upper().replace(".", "")
            hm = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_text)
            if not hm:
                continue
            hour, minute = int(hm.group(1)) % 12, int(hm.group(2))
            if hm.group(3) == "PM":
                hour += 12
            kind = _kind(" ".join(cells[2:]))
            if not kind:
                continue
            event = _event(datetime(d.year, d.month, d.day, hour, minute,
                                    tzinfo=NY), kind)
            if first_day <= event["day"] <= last_day:
                events.append(event)
        except (ValueError, OverflowError):
            continue
    return _unique(events)


def _unique(events):
    out = {(e["day"], e["clock"], e["kind"]): e for e in events}
    return sorted(out.values(), key=lambda x: (x["day"], x["clock"], x["kind"]))


def _months(first_day, last_day):
    d = date(first_day.year, first_day.month, 1)
    last = date(last_day.year, last_day.month, 1)
    while d <= last:
        yield (d.year, d.month)
        d = (d.replace(year=d.year + 1, month=1) if d.month == 12
             else d.replace(month=d.month + 1))


@st.cache_data(ttl=3600, max_entries=8, show_spinner=False)
def fetch_bls(first_day, last_day):
    """Prova ICS, poi tabella BLS ufficiale. Fail closed se entrambe falliscono."""
    if not isinstance(first_day, date) or not isinstance(last_day, date):
        raise TypeError("Intervallo date BLS obbligatorio")
    if first_day > last_day or (last_day - first_day).days > 15:
        raise ValueError("Finestra calendario BLS non valida")
    problems = []
    try:
        result = parse_ics(
            _read_url(ICS_URL, accept="text/calendar"), first_day, last_day)
        if result:
            return result
        # Alcuni feed ICS BLS contengono solo le prossime pubblicazioni.
        # Un giorno senza eventi nel feed non è prova di assenza:
        # controllare anche le tabelle mensili, senza inventare orari.
        problems.append("ICS: nessun evento nel periodo")
    except (OSError, TimeoutError, ValueError) as exc:
        problems.append("ICS: " + type(exc).__name__)
    result = []
    try:
        for year, month in _months(first_day, last_day):
            html = _read_url(MONTH_URL.format(year=year, month=month),
                             accept="text/html")
            result.extend(parse_month_html(html, first_day, last_day))
        return _unique(result)
    except (OSError, TimeoutError, ValueError) as exc:
        problems.append("HTML: " + type(exc).__name__)
    raise RuntimeError("BLS non disponibile (" + ", ".join(problems) + ")")

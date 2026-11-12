"""Calendario macro Serafini SPX: fonti pubbliche gratuite, nessuna API key.
Copertura verificabile: BLS, BEA, Census, Fed FOMC, ISM (2026).
Non interpreta assenza di eventi come garanzia che il calendario USA sia completo.
Non modifica Gamma, CBOE, Replay o pipeline dati.
"""
import re
from datetime import datetime, timedelta, date
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import streamlit as st

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")
BLS_URL = "https://www.bls.gov/schedule/news_release/"
BEA_URL = "https://www.bea.gov/news/schedule/"
CENSUS_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"
FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
ISM_URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar"
SOURCES = {"BLS": BLS_URL, "BEA": BEA_URL, "Census": CENSUS_URL,
           "Fed": FED_URL, "ISM": ISM_URL,
           "Jobless Claims": "https://fred.stlouisfed.org/releases/calendar?rid=180&y=2026"}
# Pubblicazioni ISM 2026, dal calendario ufficiale, non regole stimate.
ISM_2026 = {
    1: (5, 7), 2: (2, 4), 3: (2, 4), 4: (1, 6),
    5: (1, 5), 6: (1, 3), 7: (1, 6), 8: (3, 5),
    9: (1, 3), 10: (1, 5), 11: (2, 4), 12: (1, 3),
}
# Date di decisione FOMC 2026, pubblicate nel calendario ufficiale.
FOMC_2026 = ((1,28), (3,18), (4,29), (6,17),
             (7,29), (9,16), (10,28), (12,9))


class TableRows(HTMLParser):
    """Estrazione HTML robusta di righe e celle, senza eseguire JS remoto."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignore += 1
        elif not self.ignore and tag == "tr":
            self.row = []
        elif not self.ignore and tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.ignore:
            self.ignore -= 1
        elif not self.ignore and tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif not self.ignore and tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None and not self.ignore:
            self.cell.append(data)


def _read(url):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SerafiniMacroCalendar/2.0)",
        "Accept": "text/html",
    })
    with urlopen(request, timeout=12) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("Risposta troppo grande")
    return payload.decode("utf-8", errors="replace")


def _event(d, hour, minute, kind, provider, source, *, detail=""):
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY).astimezone(ROME)
    return {"day": local.date(), "clock": local.strftime("%H:%M"),
            "kind": kind, "priority": "ALTA", "status": "scheduled",
            "provider": provider, "source": source, "detail": detail}


def _date_month(mon, day, year):
    return datetime.strptime(f"{mon} {day} {year}", "%B %d %Y").date()


def _time_et(value):
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", value, re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return h, int(m.group(2))


def _bea_events(first, last):
    parser = TableRows()
    parser.feed(_read(BEA_URL))
    events = []
    for row in parser.rows:
        if len(row) < 2:
            continue
        joined = " | ".join(row)
        m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s+(\d{1,2}):(\d{2})\s*(AM|PM)", joined, re.I)
        if not m:
            continue
        headline = row[-1]
        if re.search(r"Personal Income and Outlays", headline, re.I):
            kind = "PCE / Redditi e consumi"
        elif re.search(r"\bGDP\b", headline, re.I):
            kind = "PIL USA (GDP)"
        elif re.search(r"International Trade in Goods and Services", headline, re.I):
            kind = "Bilancia commerciale USA"
        else:
            continue
        for year in range(first.year, last.year + 1):
            d = _date_month(m.group(1), m.group(2), year)
            if first <= d <= last:
                clock = _time_et(m.group(3) + ":" + m.group(4) + " " + m.group(5))
                if clock:
                    events.append(_event(d, *clock, kind, "BEA", BEA_URL, detail=headline))
    return events


def _census_events(first, last):
    """Calendario Census ufficiale: colonne Indicator | Release Date | Time | Period.
    Esempio URL verificato: /economic-indicators/calendar-listview-2026.html.
    Non confondere sito raggiungibile con calendario correttamente estratto.
    """
    events = []
    for year in range(first.year, last.year + 1):
        url = f"https://www.census.gov/economic-indicators/calendar-listview-{year}.html"
        parser = TableRows()
        parser.feed(_read(url))
        if not parser.rows:
            raise ValueError(f"Census {year}: nessuna tabella riconosciuta")
        recognized_rows = 0
        for row in parser.rows:
            if len(row) < 3:
                continue
            indicator, release_date, release_time = row[:3]
            m = re.search(
                r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                r"\s+(\d{1,2}),?\s+(20\d{2})\b", release_date, re.I)
            clock = _time_et(release_time)
            if not m or clock is None:
                continue
            recognized_rows += 1
            d = _date_month(*m.groups())
            if not first <= d <= last:
                continue
            low = indicator.lower()
            if "advance monthly sales for retail and food services" in low:
                kind = "Vendite al dettaglio USA (Retail Sales)"
            elif "advance report on durable goods" in low:
                kind = "Ordini beni durevoli USA"
            elif "new residential construction" in low:
                kind = "Housing Starts / Permessi edilizi"
            elif "new residential sales" in low:
                kind = "Vendite nuove abitazioni"
            elif "construction spending" in low:
                kind = "Spesa costruzioni"
            elif "manufacturing and trade: inventories and sales" in low:
                kind = "Scorte imprese USA"
            else:
                continue
            events.append(_event(d, *clock, kind, "Census", url))
        if recognized_rows == 0:
            raise ValueError(f"Census {year}: zero righe calendario interpretabili")
    return events

def _fed_events(first, last):
    # Verifica che il sito ufficiale sia raggiungibile e contenga la sezione 2026.
    content = _read(FED_URL)
    if not re.search(r"2026\s+FOMC\s+Meetings", re.sub(r"<[^>]+>", " ", content), re.I):
        raise ValueError("Calendario FOMC 2026 non riconosciuto")
    events = []
    if first.year <= 2026 <= last.year:
        for month, day in FOMC_2026:
            d = date(2026, month, day)
            if first <= d <= last:
                events.append(_event(d, 14, 0, "FOMC — decisione tassi",
                                     "Fed", FED_URL, detail="Orario standard da verificare"))
                events.append(_event(d, 14, 30, "Conferenza stampa Fed",
                                     "Fed", FED_URL, detail="Orario ordinario da verificare"))
    return events


def _ism_events(first, last):
    """Date dal calendario ufficiale ISM 2026, non richiede chiamate HTTP.
    Non estendere a 2027 senza pubblicazione di un nuovo calendario.
    """
    events = []
    if first.year <= 2026 <= last.year:
        for month, days in ISM_2026.items():
            for i, day in enumerate(days):
                d = date(2026, month, day)
                if first <= d <= last:
                    events.append(_event(
                        d, 10, 0,
                        "ISM Manufacturing PMI" if i == 0 else "ISM Services PMI",
                        "ISM", ISM_URL,
                        detail="Calendario ISM ufficiale 2026 (verifica eventuali variazioni)"))
    return events


def _claims_events(first, last):
    """Legge calendario FRED per l'anno 2026: ogni data deve comparire
    esplicitamente nella pagina 2026 del report, no ricorrenze presunte.
    Gli orari nella pagina FRED sono US Central e la pubblicazione ordinaria
    e alle 07:30 CT = 08:30 ET. Non dedurre dati effettivamente rilasciati.
    """
    url = "https://fred.stlouisfed.org/releases/calendar?rid=180&y=2026"
    html = _read(url)
    readable = re.sub(r"<[^>]*>", " ", html)
    readable = " ".join(readable.split())
    if "Unemployment Insurance Weekly Claims Report" not in readable:
        raise ValueError("Calendario FRED claims non riconosciuto")
    out = []
    names = ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December")
    for month in names:
        for m in re.finditer(r"\b" + month + r"\s+(\d{1,2}),?\s+2026\b", readable, re.I):
            snippet = readable[m.end():m.end()+190]
            if "Unemployment Insurance Weekly Claims Report" not in snippet:
                continue
            d = _date_month(month, m.group(1), "2026")
            if first <= d <= last:
                out.append(_event(d, 8, 30, "Richieste iniziali sussidio (Jobless Claims)",
                                  "FRED / DOL", url))
    return out

@st.cache_data(ttl=1800, max_entries=12, show_spinner=False)
def load_calendar(first_day, last_day):
    from bls_calendar import fetch_bls
    events, failures, healthy, counts = [], {}, [], {}
    providers = {
        "Jobless Claims": lambda: _claims_events(first_day, last_day),
        "BLS": lambda: fetch_bls(first_day, last_day),
        "BEA": lambda: _bea_events(first_day, last_day),
        "Census": lambda: _census_events(first_day, last_day),
        "Fed": lambda: _fed_events(first_day, last_day),
        "ISM": lambda: _ism_events(first_day, last_day),
    }
    for name, fetch in providers.items():
        try:
            result = fetch()
            events.extend(result)
            healthy.append(name)
            counts[name] = len(result)
        except Exception as exc:
            failures[name] = type(exc).__name__
    unique = {}
    for e in events:
        key = (e["day"], e["clock"], e["kind"])
        if key not in unique:
            unique[key] = e
    return sorted(unique.values(), key=lambda e: (e["day"], e["clock"], e["kind"])), {
        "healthy": healthy, "failed": failures, "complete": not failures,
        "sources_count": len(healthy), "total_count": len(providers),
        "counts": counts,
    }


def render():
    now = datetime.now(ROME)
    today = now.date()
    st.markdown("### Calendario macro USA — eventi da monitorare")
    st.caption("Fonti gratuite: " + " · ".join(
        f"[{name}]({url})" for name, url in SOURCES.items()
    ))
    events, health = load_calendar(today, today + timedelta(days=7))
    if not health["healthy"]:
        st.error("Fonti macro non raggiungibili. Calendario non verificabile: NON significa nessuna news.")
    elif not health["complete"]:
        st.warning("Calendario PARZIALE: " +
                   ", ".join(health["healthy"]) +
                   " disponibili; mancano: " + ", ".join(health["failed"]) +
                   ". Non escludere news USA assenti dall'elenco.")
    else:
        st.caption("Fonti verificate: " + ", ".join(health["healthy"]) +
                   ". Copertura selettiva degli eventi principali, non calendario esaustivo.")
    with st.expander("Diagnostica fonti gratuite", expanded=False):
        for name in SOURCES:
            if name in health["healthy"]:
                st.write(f"✅ {name}: fonte letta · {health['counts'].get(name, 0)} eventi nei prossimi 7 giorni")
            else:
                st.write(f"⚠️ {name}: {health['failed'].get(name, 'non disponibile')}")
    today_events = [e for e in events if e["day"] == today]
    upcoming = [e for e in events if e["day"] > today]
    from magnificent7_earnings import render as render_m7, render_upcoming as render_m7_upcoming
    with st.container(border=True):
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("**Eventi macro USA di oggi**")
            if not today_events:
                st.caption("Nessun evento rilevato nelle fonti disponibili. Non equivale a nessuna news.")
            for e in today_events:
                st.markdown(f"**{e['clock']} Roma — {e['kind']}** · {e['provider']} · [Fonte]({e['source']})")
                if e.get("detail"):
                    st.caption(e["detail"])
        with right:
            upcoming_earnings, earnings_sources = render_m7(today)
        col_macro, col_earn = st.columns(2, gap="large")
        with col_macro:
            with st.expander("Prossimi dati macro USA · 7 giorni", expanded=False):
                if not upcoming:
                    st.caption("Nessun evento nelle fonti disponibili; copertura non esaustiva.")
                for e in upcoming:
                    st.markdown(f"**{e['day']:%d/%m/%Y} · {e['clock']} Roma — {e['kind']}** · {e['provider']} · [Fonte]({e['source']})")
        with col_earn:
            with st.expander("Trimestrali Magnifiche 7 — prossimi 7 giorni", expanded=False):
                render_m7_upcoming(upcoming_earnings, earnings_sources)
    st.caption("Orari Europe/Rome, conversione America/New_York con DST automatico. "
               "Calendario eventi programmati: non conferma avvenuta pubblicazione.")

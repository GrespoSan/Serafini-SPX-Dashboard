"""Calendario macro USA essenziale, con priorita per SPX/ES e fonte libera.

Le date provengono da Finance Calendar (fonte terza, attribuzione richiesta).
Non influenzano calcoli NET GAMMA, file Cboe o acquisizioni Cloud.
"""
import json
import re
from datetime import datetime, timedelta, time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import streamlit as st

ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")
API = "https://www.financecalendar.com/wp-json/fc/v1/calendar"
CREDITS = "https://www.financecalendar.com"
OFFICIAL = {
    "CPI": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "PCE": "https://www.bea.gov/news/schedule/",
    "PPI": "https://www.bls.gov/schedule/news_release/ppi.htm",
    "Jobless Claims": "https://www.dol.gov/agencies/eta/ui-data",
    "NFP": "https://www.bls.gov/schedule/news_release/empsit.htm",
    "Retail Sales": "https://www.census.gov/retail/release_schedule.html",
    "FOMC": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "Conferenza stampa Fed": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "FOMC Minutes": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "ISM Manufacturing": "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/",
    "ISM Services": "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/",
    "JOLTS": "https://www.bls.gov/schedule/news_release/jolts.htm",
    "Consumer Confidence": "https://www.conference-board.org/topics/consumer-confidence",
    "Michigan Sentiment": "https://data.sca.isr.umich.edu/",
    "S&P Global Flash PMI": "https://www.spglobal.com/marketintelligence/en/mi/research-analysis/pmi.html",
    "Beige Book": "https://www.federalreserve.gov/monetarypolicy/beige-book-default.htm",
}
HIGH_PRIORITY = frozenset((
    "CPI", "PCE", "PPI", "Jobless Claims", "NFP", "Retail Sales",
    "FOMC", "Conferenza stampa Fed", "FOMC Minutes",
    "ISM Manufacturing", "ISM Services", "JOLTS",
))
# Altri indicatori non sostituiscono mai eventi ad alta priorita.
SECONDARY = frozenset(("Consumer Confidence", "Michigan Sentiment",
                       "S&P Global Flash PMI", "Beige Book"))


def _source_link(event, kind):
    """Solo https; la classificazione e le etichette non usano testi HTML remoti."""
    candidate = str(event.get("source_url") or "").strip()
    try:
        parsed = urlparse(candidate)
        if parsed.scheme == "https" and parsed.netloc and not any(
                ch.isspace() or ch in "()[]<>" for ch in candidate):
            return candidate
    except (TypeError, ValueError):
        pass
    return OFFICIAL[kind]


def _kind(event):
    """Classificazione conservativa per codice serie + nomi riconoscibili.

    Non identifica genericamente 'Fed', 'PMI' o 'confidence':
    evitiamo falsi FOMC e collisioni con altri paesi/indici.
    """
    code = str(event.get("series_code") or "").lower().strip()
    name = " ".join(str(event.get(k) or "") for k in
                    ("series_name", "name", "title")).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", name)

    # Ordine: verbali e conferenza prima della decisione FOMC.
    if (code in ("fomc-minutes", "fed-minutes") or
            re.search(r"\bfomc minutes\b|\bfederal open market committee minutes\b", normalized)):
        return "FOMC Minutes"
    if (code in ("fomc-press-conference", "fed-press-conference") or
            re.search(r"\b(fomc|fed|federal reserve) press conference\b", normalized)):
        return "Conferenza stampa Fed"
    if (code in ("fomc", "fed-decision", "fomc-decision", "fed-funds") or
            re.search(r"\bfomc (rate )?decision\b|\bfomc meeting (decision|statement)\b", normalized)):
        return "FOMC"
    if code in ("ism-manufacturing", "us-ism-manufacturing", "ism-manufacturing-pmi") or (
            "ism" in normalized and "manufacturing" in normalized):
        return "ISM Manufacturing"
    if code in ("ism-services", "us-ism-services", "ism-non-manufacturing") or (
            "ism" in normalized and
            ("services" in normalized or "non manufacturing" in normalized)):
        return "ISM Services"
    if code in ("jolts", "us-jolts") or re.search(
            r"\bjolts\b|\bjob openings and labor turnover\b", normalized):
        return "JOLTS"
    if code in ("consumer-confidence", "us-consumer-confidence") or (
            "consumer confidence" in normalized and
            ("conference board" in normalized or "us" in normalized or code)):
        return "Consumer Confidence"
    if code in ("michigan-sentiment", "us-michigan-sentiment",
                "university-michigan-sentiment") or (
            "michigan" in normalized and "sentiment" in normalized):
        return "Michigan Sentiment"
    if code in ("us-flash-pmi", "sp-global-flash-pmi", "us-sp-global-flash-pmi") or (
            "s p global" in normalized and "flash pmi" in normalized):
        return "S&P Global Flash PMI"
    if code in ("beige-book", "fed-beige-book") or re.search(r"\bbeige book\b", normalized):
        return "Beige Book"
    # Sei serie precedenti, stessa copertura e stessi nomi visibili.
    if code in ("us-cpi", "cpi") or re.search(r"\b(consumer price index|cpi)\b", normalized):
        return "CPI"
    if code in ("us-ppi", "ppi") or re.search(r"\b(producer price index|ppi)\b", normalized):
        return "PPI"
    if (code in ("us-pce", "us-pce-price-index", "pce") or
        re.search(r"\b(personal income and outlays|personal consumption expenditures|pce price index|core pce)\b", normalized)):
        return "PCE"
    if (re.search(r"\b(jobless claims|initial unemployment claims|initial claims)\b", normalized)
        or "jobless" in code or "initial-claims" in code):
        return "Jobless Claims"
    if (code in ("us-nfp", "nfp", "us-nonfarm-payrolls") or
        re.search(r"\b(nonfarm payrolls|non farm payrolls|employment situation)\b", normalized)):
        return "NFP"
    if (code in ("us-retail-sales", "retail-sales") or
        re.search(r"\bretail sales\b", normalized)):
        return "Retail Sales"
    return None


def _rome_time(event):
    """Mai applicare un +6 fisso: DST USA ed europeo cambiano in date diverse."""
    utc = event.get("time_utc")
    if isinstance(utc, str) and utc.strip():
        try:
            parsed = datetime.fromisoformat(utc.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(ROME)
        except ValueError:
            pass
    local = event.get("time_local")
    if isinstance(local, str) and local.strip():
        try:
            parsed = datetime.fromisoformat(local.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(ROME)
        except ValueError:
            pass
    date_str = event.get("date")
    eastern = event.get("time_et")
    if isinstance(date_str, str) and isinstance(eastern, str):
        try:
            clock = re.search(r"(\d{1,2}):(\d{2})", eastern)
            if clock:
                hour, minute = int(clock.group(1)), int(clock.group(2))
                if re.search(r"\bpm\b", eastern, re.I) and hour < 12:
                    hour += 12
                if re.search(r"\bam\b", eastern, re.I) and hour == 12:
                    hour = 0
                day = datetime.fromisoformat(date_str[:10]).date()
                return datetime.combine(day, time(hour, minute), NEW_YORK).astimezone(ROME)
        except (ValueError, TypeError):
            pass
    return None


@st.cache_data(ttl=1800, show_spinner=False, max_entries=10)
def _fetch(first_day, last_day):
    params = urlencode({
        "from": first_day, "to": last_day, "country": "US",
        "tz": "Europe/Rome", "limit": 500,
    })
    request = Request(API + "?" + params, headers={
        "Accept": "application/json", "User-Agent": "SerafiniSPXDashboard/1.0",
    })
    with urlopen(request, timeout=12) as response:
        data = json.loads(response.read(3_000_001))
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("Risposta calendario non valida")
    if data.get("total") is not None and int(data["total"]) > len(data["events"]):
        raise ValueError("Calendario incompleto: paginazione necessaria")
    return data["events"]


def _prepare(raw, today):
    result = []
    seen = set()
    for event in raw:
        if not isinstance(event, dict) or event.get("status") == "cancelled":
            continue
        # La fonte è già filtrata per country=US; ulteriore verifica se presente.
        country = str(event.get("country") or "").upper()
        if country and country not in ("US", "USA", "UNITED STATES"):
            continue
        kind = _kind(event)
        if not kind:
            continue
        dt = _rome_time(event)
        if dt is None:
            # Un orario ignoto non viene inventato; conserva la data dichiarata.
            try:
                declared = datetime.fromisoformat(str(event.get("date"))[:10]).date()
            except ValueError:
                continue
            if declared < today:
                continue
            day, clock = declared, "Orario da verificare"
        else:
            day, clock = dt.date(), dt.strftime("%H:%M")
            if day < today:
                continue
        identity = (day.isoformat(), kind, clock)
        if identity in seen:
            continue
        seen.add(identity)
        status = str(event.get("status") or "").lower()
        result.append({
            "day": day, "clock": clock, "kind": kind,
            "priority": "ALTA" if kind in HIGH_PRIORITY else "SECONDARIA",
            "status": status,
            "source": _source_link(event, kind),
        })
        # La conferenza segue alle 14:30 ET una decisione FOMC ordinaria
        # alle 14:00 ET, come da calendario Fed. Non derivarla per
        # riunioni straordinarie o decisioni con orario diverso.
        if kind == "FOMC" and dt is not None:
            et = dt.astimezone(NEW_YORK)
            if et.hour == 14 and et.minute == 0:
                press = et + timedelta(minutes=30)
                local_press = press.astimezone(ROME)
                press_key = (local_press.date().isoformat(),
                             "Conferenza stampa Fed", local_press.strftime("%H:%M"))
                if press_key not in seen:
                    seen.add(press_key)
                    result.append({
                        "day": local_press.date(),
                        "clock": local_press.strftime("%H:%M"),
                        "kind": "Conferenza stampa Fed", "priority": "ALTA",
                        "status": status,
                        "source": OFFICIAL["Conferenza stampa Fed"],
                        "inferred_schedule": "14:30 ET da FOMC ordinario 14:00 ET",
                    })
    return sorted(result, key=lambda e: (e["day"], e["clock"], e["kind"]))


def render():
    now = datetime.now(ROME)
    today = now.date()
    st.markdown(
        '<div style="display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;'
        'margin:0 0 0.65rem 0">'
        '<h3 style="margin:0;padding:0">Calendario macro USA — eventi da monitorare</h3>'
        '<span style="font-size:0.82rem;color:#999">Dati: '
        '<a href="https://www.financecalendar.com" target="_blank" '
        'rel="noopener noreferrer">Finance Calendar</a></span>'
        '</div>',
        unsafe_allow_html=True,
    )
    try:
        # Includiamo ieri UTC/NY per gestire la finestra di cambiamento DST;
        # date nel UI filtrate invece in Europe/Rome.
        events = _prepare(_fetch((today - timedelta(days=1)).isoformat(),
                                 (today + timedelta(days=7)).isoformat()), today)
    except Exception:
        st.warning("Calendario macro non verificabile in questo momento. "
                   "Non interpretare l'assenza di dati come assenza di pubblicazioni.")
        st.caption("Controlla i calendari ufficiali BLS, BEA e Census prima dell'operatività.")
        st.link_button("Calendario ufficiale BLS", "https://www.bls.gov/schedule/2026/home.htm")
        return

    today_events = [e for e in events if e["day"] == today]
    urgent = [e for e in today_events if e["priority"] == "ALTA"]
    secondary = [e for e in today_events if e["priority"] == "SECONDARIA"]

    def event_line(item, show_day=False):
        scheduled = " · orario Fed previsto" if item.get("inferred_schedule") else ""
        released = " · pubblicato (secondo il calendario)" if item["status"] == "released" else ""
        tentative = " · data/orario da verificare" if item["status"] == "estimated" else ""
        date_text = f"{item['day']:%d/%m/%Y} · " if show_day else ""
        label = f"**{date_text}{item['clock']} Roma** — **{item['kind']}**{scheduled}"
        if item["kind"] in ("FOMC", "Conferenza stampa Fed"):
            label = f":red[{label}]"
        st.markdown(
            f"{label}{released}{tentative} · [Fonte]({item['source']})"
        )

    # Scheda compatta con eventi prioritari affiancati;
    # calendari, fonti, orari e classificazioni restano invariati.
    with st.container(border=True):
        if urgent:
            st.markdown(
                f"**ATTENZIONE — {len(urgent)} "
                f"{'evento macro USA prioritario' if len(urgent) == 1 else 'eventi macro USA prioritari'} oggi**"
            )
            event_columns = st.columns(min(len(urgent), 3), gap="small")
            for i, item in enumerate(urgent):
                with event_columns[i % len(event_columns)]:
                    event_line(item)
        else:
            st.markdown("**Nessun evento macro USA ad alta priorità previsto oggi**")
        if secondary:
            with st.expander(f"Altri eventi USA di oggi · {len(secondary)}", expanded=False):
                for item in secondary:
                    event_line(item)

    upcoming = [e for e in events if e["day"] > today]
    if upcoming:
        with st.expander("Prossimi dati macro USA · 7 giorni", expanded=False):
            for item in upcoming:
                priority = " · priorità secondaria" if item["priority"] == "SECONDARIA" else ""
                st.markdown(
                    f"**{item['day']:%d/%m/%Y} · {item['clock']} Roma** — "
                    f"**{item['kind']}**{priority}"
                    f"{' · orario Fed previsto' if item.get('inferred_schedule') else ''}"
                    f" · [Fonte]({item['source']})"
                )
    st.caption("Orari Europe/Rome con ora legale automatica · "
               "FOMC: conferenza 30 minuti dopo la decisione ordinaria, "
               "da confermare sul calendario Fed.")

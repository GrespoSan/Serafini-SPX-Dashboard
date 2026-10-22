"""Avvisi per sei pubblicazioni macro USA, senza servizi a pagamento.

Le date provengono da Finance Calendar (fonte terza, attribuzione richiesta).
Non influenzano calcoli NET GAMMA, file Cboe o acquisizioni Cloud.
"""
import json
import re
from datetime import datetime, timedelta, time
from urllib.parse import urlencode
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
}


def _kind(event):
    """Usa codice serie e titolo insieme: nessuna classificazione per impatto."""
    name = " ".join(str(event.get(k) or "") for k in
                    ("series_code", "series_name", "name", "title")).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", name)
    code = str(event.get("series_code") or "").lower()
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
        result.append({
            "day": day, "clock": clock, "kind": kind,
            "status": str(event.get("status") or "").lower(),
            "source": str(event.get("source_url") or OFFICIAL[kind]),
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
    if today_events:
        st.warning(f"ATTENZIONE — Oggi {len(today_events)} pubblicazion"
                   f"{'e' if len(today_events) == 1 else 'i'} macro USA da monitorare. "
                   "Possibili movimenti rapidi del future anche prima dell'apertura cash.")
        for item in today_events:
            status = " · pubblicato (secondo il calendario)" if item["status"] == "released" else ""
            st.markdown(f"**{item['clock']} (Roma) — {item['kind']}**{status} · "
                        f"[Fonte]({item['source']})")
    else:
        st.info("Nessuno dei sei eventi macro selezionati risulta previsto oggi "
                "nel calendario consultato.")

    upcoming = [e for e in events if e["day"] > today]
    if upcoming:
        with st.expander("Prossimi dati macro USA · 7 giorni", expanded=False):
            for item in upcoming:
                st.markdown(f"**{item['day']:%d/%m/%Y} · {item['clock']} Roma** — "
                            f"{item['kind']} · [Fonte]({item['source']})")
    

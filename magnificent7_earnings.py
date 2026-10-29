"""Calendario indicativo trimestrali Magnifiche 7, indipendente dai dati Cboe."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

COMPANIES = {
    "AAPL": ("Apple", "https://investor.apple.com/"),
    "MSFT": ("Microsoft", "https://www.microsoft.com/en-us/investor"),
    "NVDA": ("NVIDIA", "https://investor.nvidia.com/"),
    "AMZN": ("Amazon", "https://ir.aboutamazon.com/"),
    "GOOGL": ("Alphabet", "https://abc.xyz/investor/"),
    "META": ("Meta", "https://investor.atmeta.com/"),
    "TSLA": ("Tesla", "https://ir.tesla.com/"),
}


@st.cache_data(ttl=21600, show_spinner=False)
def earnings_dates():
    """Fonte di calendario terza: date indicative, mai orari o conferme inventati."""
    import yfinance as yf
    rows = []
    successful = 0
    for ticker, (company, source) in COMPANIES.items():
        try:
            info = yf.Ticker(ticker).get_earnings_dates(limit=8)
            if info is None or info.empty:
                continue
            successful += 1
            for date_stamp in pd.to_datetime(info.index):
                if pd.isna(date_stamp):
                    continue
                # La data di borsa è New York; nessuna conversione dell'ora
                # perché l'ora effettiva di pubblicazione non è verificata.
                if date_stamp.tzinfo is not None:
                    event_day = date_stamp.tz_convert("America/New_York").date()
                else:
                    event_day = date_stamp.date()
                rows.append((event_day, ticker, company, source))
        except Exception:
            continue
    return sorted(set(rows)), successful


def render(today):
    try:
        rows, successful = earnings_dates()
    except Exception:
        rows, successful = [], 0
    window = today + timedelta(days=7)
    current = [r for r in rows if r[0] == today]
    upcoming = [r for r in rows if today < r[0] <= window]
    if current:
        st.markdown(f"**TRIMESTRALI OGGI — {len(current)} {'società' if len(current)==1 else 'società'}**")
        for _, ticker, company, source in current:
            st.markdown(f"**{company} ({ticker})** · data indicativa · [Investor Relations]({source})")
    elif successful == len(COMPANIES):
        st.markdown("**Nessuna trimestrale delle Magnifiche 7 indicata oggi**")
    else:
        st.markdown("**Trimestrali oggi: calendario parziale o non disponibile**")
    return upcoming, successful


def render_upcoming(upcoming, successful):
    if upcoming:
        for day, ticker, company, source in upcoming:
            st.markdown(f"**{day:%d/%m/%Y}** · {company} ({ticker}) · data indicativa · [Investor Relations]({source})")
    elif successful == len(COMPANIES):
        st.caption("Nessuna trimestrale indicata nei prossimi 7 giorni.")
    else:
        st.caption("Calendario non completo: verificare le date presso Investor Relations.")
    st.caption("Date da calendario Yahoo Finance, indicative e non confermate. Nessun orario di pubblicazione attribuito.")

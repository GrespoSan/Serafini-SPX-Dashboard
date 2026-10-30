"""Daily SPX/ES market context from Massive futures session aggregates.

Both Range Daily and prior-session direction come from the SAME ES
contract, the SAME session_end_date and trade CLOSE (not settlement).
Historical SPX context is independent from Yahoo ES=F and its rolls.
Only server-side Streamlit Secrets are read, never echoed to the UI.
"""
from datetime import date

import streamlit as st

from massive_futures_daily import (
    MassiveSourceError, previous_sessions, previous_daily_context,
)


@st.cache_data(ttl=3600, max_entries=80, show_spinner=False)
def _fetch_massive_es(target_day: date, ticker: str, key: str):
    return previous_daily_context(previous_sessions(ticker, target_day, key))


def previous_es_daily_range(target_day: date):
    """ESZ6 etc: previous TWO completed Massive futures sessions."""
    if not isinstance(target_day, date):
        raise ValueError("Data di riferimento Daily non valida.")
    try:
        api_key = str(st.secrets.get("MASSIVE_API_KEY", "")).strip()
        contract = str(st.secrets.get("MASSIVE_ES_CONTRACT", "")).strip().upper()
    except Exception:
        api_key, contract = "", ""
    if not api_key:
        raise MassiveSourceError(
            "MASSIVE_API_KEY mancante: configura una chiave Futures nei Secrets SPX."
        )
    if not contract:
        raise MassiveSourceError(
            "MASSIVE_ES_CONTRACT mancante: seleziona il contratto ES reale (es. ESZ6)."
        )
    return _fetch_massive_es(target_day, contract, api_key)

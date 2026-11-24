"""Servizi GEX CSV per elaborazione e cache, separati dal pannello UI.

Importare qui (non da csv_gex_panel) durante il caricamento del CSV:
il modulo di presentazione non deve poter bloccare NET GAMMA/Pine.
"""
from __future__ import annotations

import streamlit as st

from csv_gex_engine import analyze_csv_gex
from csv_gex_oi import fetch_occ_oi


OI_LABELS = {
    "Auto · OCC se confrontabile, altrimenti CSV": "auto",
    "OCC Series Search · solo OCC, nessun fallback": "occ",
    "CSV Cboe · Open Interest del file": "csv",
}
DEFAULT_OI_LABEL = "Auto · OCC se confrontabile, altrimenti CSV"


@st.cache_data(show_spinner=False, max_entries=6, ttl=3600)
def _cached_gex(blob, symbol, units, oi_mode="csv", occ_values=None,
                occ_provenance=None, occ_error=None):
    return analyze_csv_gex(
        blob, symbol, units, oi_mode=oi_mode,
        occ_values=occ_values, occ_provenance=occ_provenance,
        occ_error=occ_error,
    )


@st.cache_data(show_spinner=False, max_entries=8, ttl=900)
def _cached_occ(symbol, accepted):
    """OCC corrente, cache 15 minuti; NON usabile per CSV storici."""
    return fetch_occ_oi(symbol, set(accepted))

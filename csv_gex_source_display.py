"""Provenienza e orari GEX: solo metadati verificabili, nessun calcolo.

La fotografia Cboe fissa l'ora del prezzo/IV/contratti; l'OI OCC puo'
essere scaricato in un altro momento senza che sia nota la data OI as-of.
Alpaca non e' una fonte dati usata da Serafini · Analisi CSV.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


ROME = ZoneInfo("Europe/Rome")


def _occ_download_rome(iso_utc):
    """Converte esclusivamente timestamp OCC con fuso esplicito."""
    if not iso_utc:
        return "N/D (nessun download OCC utilizzato)"
    try:
        value = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            return "N/D (timestamp OCC senza fuso verificato)"
        return value.astimezone(ROME).strftime("%d/%m/%Y %H:%M:%S Roma")
    except (ValueError, TypeError, OverflowError):
        return "N/D (timestamp download OCC non verificabile)"


def describe_gex_sources(info: dict) -> dict[str, str]:
    """Fonte *effettiva* (dopo Auto/fallback) e orari, non fonte selezionata.

    Il modello BSM/Edge-style e' separato dalle sorgenti dati.
    """
    source = info.get("source")
    snapshot_clock = (str(info.get("timestamp_rome") or "").strip()
                      or "N/D (fotografia CSV non verificata)")
    if snapshot_clock != "N/D (fotografia CSV non verificata)":
        snapshot_clock += " Roma"
    oi = info.get("oi_info") or {}
    effective = oi.get("effective_source")
    if source != "CBOE_CSV_MANUAL_USER_EXPORT":
        title = "DATI GEX: FONTE NON VERIFICATA"
        snapshot_desc = "Fonte CSV non certificata dal parser"
    else:
        snapshot_desc = "CSV Cboe (prezzo, IV e contratti)"
        if effective == "OCC_SERIES_SEARCH":
            title = "DATI GEX: CSV CBOE + OPEN INTEREST OCC"
        elif effective == "CSV_CBOE":
            title = "DATI GEX: SOLO CSV CBOE"
        else:
            title = "DATI GEX: FONTE OPEN INTEREST NON VERIFICATA"

    if effective == "OCC_SERIES_SEARCH":
        oi_desc = "OCC Series Search (OI utilizzato nel calcolo)"
        oi_clock = _occ_download_rome(oi.get("occ_download_utc"))
        vintage = ("N/D — OCC non pubblica la data di riferimento "
                   "dell'Open Interest nel report")
    elif effective == "CSV_CBOE":
        oi_desc = "CSV Cboe (OI utilizzato nel calcolo)"
        oi_clock = "Nessun download OCC utilizzato"
        vintage = "N/D — data OI non documentata nel CSV"
    else:
        oi_desc = "Fonte Open Interest non verificata"
        oi_clock = "N/D"
        vintage = "N/D — fonte OI non verificata"

    return {
        "title": title,
        "snapshot_source": snapshot_desc,
        "snapshot_clock": snapshot_clock,
        "oi_source": oi_desc,
        "oi_download_clock": oi_clock,
        "oi_vintage": vintage,
        "alpaca": "NON UTILIZZATA — il motore Edge-style è solo un modello matematico",
        "note": ("L'orario OCC indica il download, non l'aggiornamento degli OI. "
                 "La coincidenza della seduta non certifica la sincronizzazione."),
    }

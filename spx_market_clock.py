"""Orari delle SPXW calcolati con i fusi ufficiali, senza offset fissi.

Regola ordinaria Cboe: le opzioni SPXW PM-settled in scadenza terminano
la negoziazione alle 16:00 America/New_York nel giorno di scadenza.
Le mezze giornate di mercato richiedono un calendario ufficiale distinto
(chiusura anticipata alle 13:00 ET): non sono inferite qui.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")


def cash_open_rome(day):
    """Apertura ordinaria 09:30 New York convertita in ora italiana."""
    return datetime.combine(day, time(9, 30), tzinfo=NEW_YORK).astimezone(ROME)


def spxw_expiry_close_rome(day):
    """Fine negoziazione 0DTE SPXW ordinaria 16:00 New York."""
    return datetime.combine(day, time(16, 0), tzinfo=NEW_YORK).astimezone(ROME)


def classify_0dte_download(day, download):
    """Stato informativo basato sul DOWNLOAD, non sul timestamp Cboe ambiguo.

    Se non è possibile verificare il fuso dell'acquisizione, non inventa
    l'orario e restituisce stato NON VERIFICABILE.
    """
    if isinstance(download, str):
        try:
            download = datetime.fromisoformat(download.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            download = None
    if not isinstance(download, datetime) or download.tzinfo is None:
        return {
            "status": "ORARIO_NON_VERIFICABILE",
            "post_expiry": False,
            "valid_for_pre_expiry_analysis": False,
            "download_new_york": None,
            "expiry_close_rome": spxw_expiry_close_rome(day).isoformat(),
        }
    local = download.astimezone(ROME)
    ny = download.astimezone(NEW_YORK)
    close = spxw_expiry_close_rome(day)
    expired = local >= close
    return {
        "status": "POST_SCADENZA_0DTE" if expired else "PRIMA_DELLA_SCADENZA",
        "post_expiry": expired,
        "valid_for_pre_expiry_analysis": not expired,
        "download_rome": local.isoformat(timespec="seconds"),
        "download_new_york": ny.isoformat(timespec="seconds"),
        "cash_open_rome": cash_open_rome(day).isoformat(),
        "expiry_close_rome": close.isoformat(),
        "expiry_close_new_york": datetime.combine(
            day, time(16), tzinfo=NEW_YORK).isoformat(),
        "rule": "SPXW ultimo giorno: 16:00 America/New_York (seduta ordinaria)",
        "note": ("Classification by acquisition timestamp; "
                 "half-day early closes are not automatically detected."),
    }

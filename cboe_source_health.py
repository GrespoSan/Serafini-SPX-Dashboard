"""Read-only classification of failed official Cboe SPX downloads.

Do not create a current snapshot, infer the Cboe timestamp timezone, or
confuse an HTTP 403 with an error in Serafini/GEX computation.
"""
import json
from datetime import date

SUCCESS = frozenset(("NUOVO", "INVARIATO"))


def blocked_acquisition_today(week, log_names, store, today):
    """Last real acquisition attempt today; only classify a verified Cboe 403."""
    if not isinstance(today, date):
        raise TypeError("Expected explicit Europe/Rome date")
    iso = today.isocalendar()
    if week != f"{iso.year}-W{iso.week:02d}":
        return None
    prefix = today.strftime("%Y%m%d") + "T"
    todays = sorted((name for name in log_names
                     if isinstance(name, str)
                     and name.startswith(prefix) and name.endswith(".json")),
                    reverse=True)
    for name in todays:
        try:
            report = json.loads(store.download(f"{week}/logs/{name}"))
            if not isinstance(report, dict) or report.get("rome_date") != today.isoformat():
                continue
            status = report.get("status")
            if status in SUCCESS:
                return None
            err = str(report.get("error") or "")
            if status == "ERRORE" and "HTTPError: HTTP Error 403" in err:
                return {
                    "download_rome": report.get("download_rome"),
                    "slot": report.get("scheduled_slot"),
                }
            return None  # Different failure, never claim specifically Cboe 403
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            continue
    return None

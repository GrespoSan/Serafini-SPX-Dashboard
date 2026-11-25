"""Verified Cboe-acquisition clock for Multi-Market Pine v6 presentation.

The Cboe JSON timestamp has no certified timezone. Never treat it as UTC or
infer an acquisition time from option expiry, current time, or chart time.
Use the original archive filename's UTC acquisition instant or a directly
recorded timezone-aware acquisition timestamp. Europe/Rome zoneinfo handles
both CET/CEST and the weeks when US and EU DST changes differ.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
UNVERIFIED = "ora acquisizione non verificata"


def _parse_acquisition(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # Legacy NDX direct-download display format, saved as local Rome
            # wall time. Reject its ambiguous fall-back hour and DST gaps.
            if not raw.endswith(" Roma"):
                return None
            try:
                naive = datetime.strptime(raw, "%d/%m/%Y %H:%M:%S Roma")
            except ValueError:
                return None
            early = naive.replace(tzinfo=ROME, fold=0)
            late = naive.replace(tzinfo=ROME, fold=1)
            if early.utcoffset() != late.utcoffset():
                return None
            if early.astimezone(timezone.utc).astimezone(ROME).replace(
                    tzinfo=None) != naive:
                return None
            parsed = early
    else:
        return None
    # A timezone-naive Cboe timestamp is not an acquisition timestamp.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(ROME)


def pine_capture_clock(value):
    """Clock and full Rome-local date of a VERIFIED acquisition.

    An unavailable value is explicitly marked unknown, never replaced by now.
    """
    stamp = _parse_acquisition(value)
    if stamp is None:
        return {
            "verified": False,
            "clock": UNVERIFIED,
            "stamp": UNVERIFIED,
        }
    zone = stamp.tzname()
    return {
        "verified": True,
        "clock": f"{stamp:%H:%M} {zone}",
        "stamp": f"{stamp:%d/%m/%Y %H:%M} {zone}",
    }

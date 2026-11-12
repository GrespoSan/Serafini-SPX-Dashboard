"""Select one newest verified Edge Cboe reading, regardless of ingestion channel.

Only Edge visualization is affected: the Serafini NET GAMMA snapshot selector,
formula and workflow are deliberately not modified.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from edge_replay_data import SLOT_FILE, read_pair

ROME = ZoneInfo("Europe/Rome")


def latest_gex_reading(week, store, logs):
    """Compare actual, timezone-aware download clocks, never filename Cboe times.

    An official candidate requires a successful registered acquisition and an
    existing original chain. A Replay candidate requires a valid compact/Edge
    pair with matching source SHA, expiry and download timestamp.
    """
    from gamma_section import _snapshot_log_index

    now = datetime.now(ROME)
    candidates = []
    snapshots = {
        str(item.get("name"))
        for item in store.list(f"{week}/snapshots/")
        if str(item.get("name", "")).startswith("SPX_CBOE_")
    }
    index = _snapshot_log_index(
        week, tuple(str(item["name"]) for item in logs or []), store)
    for path, (label, _, clock) in index.items():
        filename = path.rsplit("/", 1)[-1]
        if (not path.startswith(f"{week}/snapshots/")
                or filename not in snapshots
                or not isinstance(clock, datetime) or clock.tzinfo is None):
            continue
        stamp = clock.astimezone(ROME)
        if stamp > now + timedelta(minutes=5):
            continue
        candidates.append({
            "kind": "Ufficiale", "day": stamp.date(),
            "clock": stamp, "slot": label, "filename": filename,
        })

    # Replays are named by their nominal slot. Their VERIFIED real download
    # clock is read from the matching saved pair before comparing to official.
    try:
        files = store.list(f"replay-edge/{week}/")
    except (RuntimeError, ValueError, TypeError, KeyError, OSError):
        files = []
    names = []
    for item in files:
        name = str(item.get("name", ""))
        match = SLOT_FILE.fullmatch(name)
        if match:
            try:
                day = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            names.append((day, f"{match.group(2)}:{match.group(3)}"))
    # The latest valid pair per date is sufficient: a later date dominates;
    # all existing slots of that day are considered if the latest is invalid.
    replay = None
    for day in sorted({d for d, _ in names}, reverse=True):
        for slot in sorted((s for d, s in names if d == day), reverse=True):
            try:
                _, _, _, clock = read_pair(store, week, day, slot)
            except (RuntimeError, ValueError, TypeError, KeyError,
                    OverflowError, OSError):
                continue
            if clock > now + timedelta(minutes=5):
                continue
            if replay is None or clock > replay["clock"]:
                replay = {
                    "kind": "Replay", "day": day,
                    "clock": clock, "slot": slot, "filename": None,
                }
        if replay is not None:
            break
    if replay is not None:
        candidates.append(replay)
    if not candidates:
        return None
    # On exact timestamp equality, original official download wins.
    return max(candidates,
               key=lambda entry: (entry["clock"],
                                  entry["kind"] == "Ufficiale"))

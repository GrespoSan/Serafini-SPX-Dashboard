"""Private, disk-backed Massive Futures response cache, expiring every Rome day.

Only finite, fully fetched Massive FUTURES JSON responses are cached. These
temporary data are NOT Cboe snapshots, not option data, not historical archives.
Every read and every app startup removes the prior day's SQLite files. A cache
is shared between Streamlit user sessions and survives process restarts on the
same filesystem, but NOT Streamlit Cloud redeploys/ephemeral disk replacement.
Never put this cache in git, Supabase, or the 30-day encrypted Cboe archive.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import os
import sqlite3
import tempfile
import time

import requests

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")
_LOCK = RLock()
_LAST_ROMA_DAY = None
ROOT = Path(tempfile.gettempdir()) / "v2_mm_massive_daily"
NAME = "massive-"
MAX_JSON_BYTES = 10 * 1024 * 1024


def _rome_day(now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Data della cache senza timezone.")
    return now.astimezone(ROME).date()


def _dir():
    # Optional override for isolated offline tests. No HTTP secrets here.
    return Path(os.environ.get("V2_MASSIVE_CACHE_DIR") or ROOT)


def purge_expired(now=None):
    """Delete prior day data on access and report process-wide day rollover."""
    global _LAST_ROMA_DAY
    today = _rome_day(now).strftime("%Y%m%d")
    folder = _dir()
    with _LOCK:
        switched = _LAST_ROMA_DAY is not None and _LAST_ROMA_DAY != today
        _LAST_ROMA_DAY = today
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for old in folder.glob(NAME + "*.sqlite3"):
            if old.name != f"{NAME}{today}.sqlite3":
                # Only files inside the dedicated cache folder, exact pattern.
                for suffix in ("", "-wal", "-shm"):
                    item = Path(str(old) + suffix)
                    try:
                        item.unlink(missing_ok=True)
                    except OSError:
                        pass
        return switched


def _db(now=None):
    purge_expired(now)
    day = _rome_day(now).strftime("%Y%m%d")
    path = _dir() / f"{NAME}{day}.sqlite3"
    con = sqlite3.connect(str(path), timeout=8)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    con.execute("CREATE TABLE IF NOT EXISTS responses ("
                "key TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "expires REAL NOT NULL)")
    return con


def _key(url, params, api_key):
    host = urlsplit(str(url))
    if (host.scheme != "https" or host.hostname != "api.massive.com"
            or not host.path.startswith("/futures/v1/") or host.query
            or host.fragment or not str(api_key or "").strip()):
        raise ValueError("Cache Massive: endpoint futures o credenziale non validi.")
    # Never persist the API key: only a one-way digest of URL + params + key.
    canonical = json.dumps([url, sorted((str(k),str(v)) for k,v in params.items()),
                            sha256(str(api_key).encode()).hexdigest()],
                           sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def _expiry(params, now):
    """Historical finalized sessions survive until Rome midnight; live 90s."""
    midnight = datetime.combine(
        _rome_day(now) + timedelta(days=1), datetime.min.time(), ROME
    ).timestamp()
    stamp = params.get("date") or params.get("window_start.lte")
    if stamp is None and params.get("window_start.lt"):
        try:
            stamp = (datetime.fromisoformat(str(params["window_start.lt"])).date()
                     - timedelta(days=1)).isoformat()
        except (TypeError, ValueError):
            stamp = None
    try:
        target = datetime.fromisoformat(str(stamp)).date() if stamp else None
    except ValueError:
        target = None
    if target is not None and target < now.astimezone(NY).date():
        return midnight
    # Prevent reusing a still-open current futures session as a final result.
    return min(midnight, time.time() + 90)


def fetch_json(url, params, api_key, get=None):
    """Return API JSON; store only validated successful full-page Futures data.

    An injected getter for tests is always called directly and never cached.
    A provider error is never converted to a fabricated or previous-day value.
    """
    params = dict(params)
    key = _key(url, params, api_key)
    requester = get or requests.get
    if get is not None:
        response = requester(url, params=params,
                             headers={"Authorization": "Bearer " + api_key,
                                      "Accept": "application/json"},
                             timeout=(6, 20))
        response.raise_for_status()
        return response.json()
    with _LOCK:
        # One request at a time per Streamlit process, preventing rerun races.
        try:
            con = _db()
        except (OSError, sqlite3.Error):
            con = None
        try:
            if con is not None:
                saved = con.execute(
                    "SELECT payload FROM responses WHERE key=? AND expires>?",
                    (key, time.time())).fetchone()
                if saved:
                    return json.loads(saved[0])
            response = requester(url, params=params,
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Accept": "application/json"},
                                 timeout=(6, 20))
            response.raise_for_status()
            data = response.json()
            # Store only complete successful lists, not 429, empty, partial or
            # authentication errors; leave source validation to caller too.
            if (con is not None and isinstance(data, dict)
                    and data.get("status") in ("OK", "DELAYED")
                    and isinstance(data.get("results"), list)
                    and data["results"] and not data.get("next_url")):
                raw = json.dumps(data, separators=(",", ":"), allow_nan=False)
                if len(raw.encode()) <= MAX_JSON_BYTES:
                    con.execute(
                        "INSERT OR REPLACE INTO responses(key,payload,expires)"
                        " VALUES(?,?,?)", (key, raw, _expiry(params, datetime.now(timezone.utc))))
                    con.commit()
            return data
        finally:
            if con is not None:
                con.close()


def invalidate_ratio_sessions(state, now=None):
    """At next Rome day remove frozen market mappings, never source snapshots."""
    current = _rome_day(now).isoformat()
    stamp = "_v2_massive_session_rome_day"
    previous = state.get(stamp)
    if previous and previous != current:
        for symbol in ("uso", "gld"):
            for ending in ("locked", "tried", "error"):
                state.pop(f"{symbol}_ratio_{ending}", None)
        for name in ("ndx_basis_result", "ndx_basis_value",
                     "ndx_basis_attempted", "ndx_basis_error"):
            state.pop(name, None)
    state[stamp] = current
    return bool(previous and previous != current)

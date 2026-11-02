"""Private SPX Massive ES daily-response cache; never stores Cboe/Supabase data.

Successful complete Futures responses are shared across Streamlit sessions in
the same deployment. SQLite survives a process restart if the temporary disk
survives, but Streamlit Cloud filesystem replacement is not durable storage.
At the first access on a new Europe/Rome calendar day, yesterday's temporary
SQLite file is deleted. No scheduled job or Secrets change is required.
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
_LOCK = RLock()
_LAST_DAY = None
ROOT = Path(tempfile.gettempdir()) / "serafini_spx_massive_day_cache"
FILE_PREFIX = "massive-es-"
MAX_JSON_BYTES = 10 * 1024 * 1024


def _rome_day(now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Cache ES: data priva di fuso orario.")
    return now.astimezone(ROME).date()


def _folder():
    # Override only for isolated offline test runs, never a storage account.
    return Path(os.environ.get("SPX_MASSIVE_CACHE_DIR") or ROOT)


def purge_expired(now=None):
    """Delete only temporary ES cache files from prior Rome days on access."""
    global _LAST_DAY
    day = _rome_day(now).strftime("%Y%m%d")
    with _LOCK:
        changed = _LAST_DAY is not None and _LAST_DAY != day
        _LAST_DAY = day
        folder = _folder()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for old in folder.glob(FILE_PREFIX + "*.sqlite3"):
            if old.name != f"{FILE_PREFIX}{day}.sqlite3":
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass
        return changed


def _key(url, params, api_key):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "api.massive.com"
            or not parsed.path.startswith("/futures/v1/aggs/ES")
            or parsed.query or parsed.fragment
            or not str(api_key or "").strip()):
        raise ValueError("Cache ES: sorgente Futures o chiave non valide.")
    # API key is never written to the SQLite file.
    payload = json.dumps([url, sorted((str(k), str(v)) for k,v in params.items()),
                          sha256(str(api_key).encode()).hexdigest()],
                         separators=(",", ":"), sort_keys=True)
    return sha256(payload.encode()).hexdigest()


def fetch_json(url, params, api_key, get=None):
    """Fetch complete ES Futures JSON once per request/day, fail closed.

    A test-injected HTTP getter bypasses the cache. Incomplete, empty and
    unsuccessful responses are not persisted. Provider 429 is not retried.
    """
    params = dict(params)
    lookup = _key(url, params, api_key)
    getter = get or requests.get
    headers = {"Authorization": "Bearer " + api_key, "Accept": "application/json"}
    if get is not None:
        response = getter(url, params=params, headers=headers, timeout=(6, 18))
        response.raise_for_status()
        return response.json()
    with _LOCK:
        con = None
        try:
            purge_expired()
            path = _folder() / (FILE_PREFIX + _rome_day().strftime("%Y%m%d") + ".sqlite3")
            con = sqlite3.connect(str(path), timeout=8)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            con.execute("CREATE TABLE IF NOT EXISTS responses ("
                        "key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            saved = con.execute(
                "SELECT payload FROM responses WHERE key=?", (lookup,)).fetchone()
            if saved:
                return json.loads(saved[0])
        except (OSError, sqlite3.Error, ValueError):
            if con is not None:
                con.close()
            con = None
        try:
            response = getter(url, params=params, headers=headers, timeout=(6, 18))
            response.raise_for_status()
            payload = response.json()
            if (con is not None and isinstance(payload, dict)
                    and payload.get("status") in ("OK", "DELAYED")
                    and isinstance(payload.get("results"), list)
                    and payload["results"] and not payload.get("next_url")):
                raw = json.dumps(payload, separators=(",", ":"), allow_nan=False)
                if len(raw.encode()) <= MAX_JSON_BYTES:
                    con.execute("INSERT OR REPLACE INTO responses(key, payload)"
                                " VALUES(?,?)", (lookup, raw))
                    con.commit()
            return payload
        finally:
            if con is not None:
                con.close()

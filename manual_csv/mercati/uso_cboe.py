"""Cboe USO original JSON/.json.gz parser, on-demand fetch, and NET GAMMA.

The data loader only runs from the explicit user refresh action; no cron,
background polling, or login/captcha bypass. Nasdaq and SPX are untouched.
"""
from collections import defaultdict
from datetime import date, timedelta
import gzip
import json
import math
import re
import requests

USO_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/USO.json"
USO_OCC = re.compile(r"^USO(\d{6})([CP])(\d{8})$")
MAX_RAW_BYTES = 50 * 1024 * 1024
MIN_VALID_COVERAGE = 0.70



def download_uso(limit_mb=50, session=None) -> bytes:
    from manual_csv.mercati.cboe_acquisition_pause import require_cboe_acquisition_enabled
    require_cboe_acquisition_enabled()
    """One explicit, bounded Cboe USO request (no unattended collection).

    Preserve the response bytes; validate USO chain before exposing it.
    If the provider restricts server-side access, surface the error and
    leave manual source import available.
    """
    session = session or requests
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
               "Cache-Control": "no-cache"}
    try:
        with session.get(USO_URL, headers=headers, timeout=(8, 55), stream=True) as response:
            if response.status_code in (401, 403, 429):
                raise RuntimeError(
                    f"Cboe HTTP {response.status_code}: acquisizione diretta non consentita "
                    "da questo server. Usa l'importazione del JSON originale."
                )
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > min(limit_mb * 1024 * 1024, MAX_RAW_BYTES):
                    raise ValueError("USO.json supera il limite di 50 MB; download annullato.")
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise RuntimeError(f"Download Cboe USO non disponibile: {str(exc)[:180]}") from exc
    if not chunks:
        raise ValueError("Cboe ha restituito un file USO vuoto.")
    blob = b"".join(chunks)
    read_uso(blob)  # blocks HTML, NDX/SPX and malformed/empty option chains
    return blob

def read_uso(blob: bytes) -> dict:
    if not isinstance(blob, bytes) or not blob or len(blob) > MAX_RAW_BYTES:
        raise ValueError("File USO assente o troppo grande (max 50 MB).")
    if blob[:2] == b"\x1f\x8b":
        # Bound inflated gzip size to prevent excessively large uploads.
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(blob)) as f:
            blob = f.read(MAX_RAW_BYTES + 1)
        if len(blob) > MAX_RAW_BYTES:
            raise ValueError("JSON USO decompresso troppo grande.")
    try:
        doc = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Il file non contiene JSON Cboe valido.") from exc
    if not isinstance(doc, dict):
        raise ValueError("Il file Cboe deve contenere un oggetto JSON.")
    symbol = str(doc.get("symbol") or "").strip().upper().lstrip("_")
    if symbol != "USO":
        raise ValueError(f"Atteso USO Cboe, trovato {symbol or 'simbolo assente'}.")
    inner = doc.get("data")
    if not isinstance(inner, dict) or not isinstance(inner.get("options"), list):
        raise ValueError("Catena opzioni USO assente o malformata.")
    if not inner["options"]:
        raise ValueError("Catena USO vuota.")
    return doc


def parse_contract(code):
    match = USO_OCC.fullmatch(str(code or "").replace(" ", "").strip().upper())
    if not match:
        return None
    raw_date, cp, raw_strike = match.groups()
    try:
        exp = date(2000 + int(raw_date[:2]), int(raw_date[2:4]), int(raw_date[4:6]))
    except ValueError:
        return None
    return exp, ("call" if cp == "C" else "put"), int(raw_strike) / 1000.0


def expirations(doc):
    return sorted({parts[0] for item in doc["data"]["options"]
                   if isinstance(item, dict)
                   if (parts := parse_contract(item.get("option"))) is not None})


def next_weekly(day, available):
    delta = (4 - day.weekday()) % 7 or 7
    chosen = day + timedelta(days=delta)
    return chosen if chosen in available else None


def _measure(value):
    try:
        if value is None or value == "":
            return None
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def close_previous(doc):
    value = _measure(doc["data"].get("prev_day_close"))
    return value if value is not None and value > 0 else None


def current_price(doc):
    value = _measure(doc["data"].get("current_price"))
    return value if value is not None and value > 0 else None


def profile(doc, expiry, reference, band_pct=3.0):
    """Use real USO strikes in previous-close ±%, without 25+25 SPX limit.

    Missing Gamma/OI/volume is EXCLUDED rather than silently filled with zero.
    Genuine zero values are permitted when supplied explicitly. Individual
    missing sides and incompleteness are reported.
    """
    if not isinstance(expiry, date):
        raise ValueError("Scadenza USO non valida.")
    if not isinstance(reference, (float, int)) or not math.isfinite(reference) or reference <= 0:
        raise ValueError("Close precedente USO non valido.")
    if not isinstance(band_pct, (int, float)) or not math.isfinite(band_pct) or not 0 < band_pct <= 10:
        raise ValueError("Fascia USO non valida.")
    low = reference * (1 - band_pct / 100.0)
    high = reference * (1 + band_pct / 100.0)
    matched = 0
    complete = 0
    seen = set()
    positive_gammas = 0
    missing = defaultdict(int)
    pairs = defaultdict(dict)
    for item in doc["data"]["options"]:
        if not isinstance(item, dict):
            continue
        parts = parse_contract(item.get("option"))
        if parts is None or parts[0] != expiry:
            continue
        exp, side, strike = parts
        if not low - 1e-8 <= strike <= high + 1e-8:
            continue
        matched += 1
        code = str(item.get("option") or "").replace(" ", "").upper()
        if code in seen or side in pairs[strike]:
            raise ValueError("Catena USO con contratto/strike/lato duplicato.")
        seen.add(code)
        gamma = _measure(item.get("gamma"))
        oi = _measure(item.get("open_interest"))
        volume = _measure(item.get("volume"))
        if gamma is None:
            missing["Gamma"] += 1
        if oi is None:
            missing["Open Interest"] += 1
        if volume is None:
            missing["Volume"] += 1
        if gamma is None or oi is None or volume is None:
            continue
        complete += 1
        if gamma > 0:
            positive_gammas += 1
        pairs[strike][side] = {"weighted": (oi + volume) * gamma, "gamma": gamma,
                               "oi": oi, "volume": volume}
    rows = []
    for strike, sides in sorted(pairs.items()):
        if "call" not in sides or "put" not in sides:
            missing["Strike senza entrambi i lati"] += 1
            continue
        c, p = sides["call"], sides["put"]
        rows.append({"strike": strike, "call": c["weighted"], "put": p["weighted"],
                     "net": c["weighted"] - p["weighted"],
                     "call_oi": c["oi"], "put_oi": p["oi"],
                     "call_volume": c["volume"], "put_volume": p["volume"],
                     "call_gamma": c["gamma"], "put_gamma": p["gamma"]})
    coverage = complete / matched if matched else 0.0
    usable = (matched > 0 and positive_gammas > 0
              and coverage >= MIN_VALID_COVERAGE and len(rows) >= 2)
    return {"expiry": expiry.isoformat(), "rows": rows if usable else [],
            "eligible": matched, "valid_contracts": complete,
            "complete_strikes": len(rows), "coverage": coverage,
            "missing": dict(missing), "positive_gammas": positive_gammas,
            "usable": usable, "lower": low, "upper": high,
            "source": "Cboe USO · Gamma presente nella fonte", "approximate": False}

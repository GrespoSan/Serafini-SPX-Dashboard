"""Read-only, SHA-bound GEX Wall/Flip fallback from verified MT4 100-strike feed.

This CSV is produced by the independent Edge engine on the official Cboe
bytes. We accept it ONLY for the same snapshot/slot/expiry, never as an
unconditional 'latest' replacement. No Serafini formula changes.
"""
import csv
import hashlib
import io
import math


def verified_official_gex_csv(csv_blob, raw_cboe, day, slot, cboe_stamp, clock=None):
    try:
        text = csv_blob.decode("utf-8-sig") if isinstance(csv_blob, bytes) else str(csv_blob)
        rows = csv.reader(io.StringIO(text))
        metadata = {}
        for cells in rows:
            if cells and cells[0] == "profile":
                break
            if len(cells) != 2 or cells[0] in metadata:
                return None
            metadata[cells[0]] = cells[1]
        normalized_slot = slot if ":" in slot else (
            {"0730": "07:30", "1550": "15:50", "1700": "17:00"}.get(slot, slot))
        if (metadata.get("schema") != "serafini_mt4_v4"
                or metadata.get("mode") != "Ufficiale"
                or metadata.get("date_rome") != day.isoformat()
                or metadata.get("slot_rome") != normalized_slot
                or metadata.get("gex_model") != "EDGE_0DTE"
                or metadata.get("gex_expiry") != day.isoformat()
                or metadata.get("gex_source_sha256") != hashlib.sha256(raw_cboe).hexdigest()
                or metadata.get("gex_cboe_timestamp") != str(cboe_stamp)
                or (clock and metadata.get("download_clock_rome") != clock)):
            return None
        out = {}
        for name in ("call_wall_spx", "put_wall_spx", "gamma_flip_spx"):
            try:
                value = float(metadata["gex_" + name])
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            if math.isfinite(value) and value > 0:
                out[name] = value
        return out or None
    except (ValueError, UnicodeError, csv.Error, AttributeError):
        return None

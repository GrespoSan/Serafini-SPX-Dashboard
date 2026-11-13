"""Verified GEX Edge levels for static SPX Pine exports.

An exported Pine is bound to ONE immutable Cboe acquisition, not today's
latest reading. Never substitute another slot, fake a missing Gamma Flip, or
derive Edge Wall/Flip from Serafini's 25+25 NET GAMMA bars.
"""
import hashlib
import json
import math
from datetime import date

from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
LEVEL_FIELDS = ("call_wall_spx", "put_wall_spx", "gamma_flip_spx")


def _clean_levels(report, day, spot=None, sha256=None):
    if (not isinstance(report, dict) or report.get("symbol") != "SPX"
            or report.get("mode") != "0DTE"
            or report.get("expiries") != [day.isoformat()]
            or report.get("quality_sufficient") is not True):
        return None
    if sha256 is not None and report.get("sha256") != sha256:
        return None
    if spot is not None:
        try:
            if not math.isclose(float(spot), float(report["spot_spx"]),
                                abs_tol=0.01):
                return None
        except (ValueError, TypeError, KeyError, OverflowError):
            return None
    out = {}
    for key in LEVEL_FIELDS:
        value = report.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number) and number > 0:
            out[key] = number
    return out


def levels_for_export(store, week, day: date, slot, frame, logs):
    """Return original Edge SPX levels of the exact selected export frame.

    On unavailable/invalid GEX, return None; the NET GAMMA export is unchanged.
    """
    if not isinstance(day, date) or not isinstance(frame, dict):
        return None
    source_clock = frame.get("clock")
    cboe_stamp = frame.get("cboe_timestamp_original")
    source_spot = frame.get("spot")
    if not source_clock or not cboe_stamp or source_spot is None:
        return None

    if frame.get("origin") in ("UFFICIALE", "STUDIO STORICO DA ARCHIVIO"):
        # Reuse the exact original selected and validated by _official_frames.
        # A SECOND scan of the scheduled logs could miss the correct file due
        # to timestamp formatting / cron alias differences and quietly
        # generate 'na' for every GEX level despite a valid Edge report.
        path = frame.get("snapshot_path")
        if (frame.get("snapshot_download_clock") != source_clock
                or not isinstance(path, str)
                or not path.startswith(f"{week}/snapshots/SPX_CBOE_")
                or not path.endswith(".json.gz")):
            return None
        try:
            raw = store.download(path)
            from net_gamma import read_snapshot, profile
            snap = read_snapshot(raw)
            if (str(snap.get("timestamp")) != str(cboe_stamp)
                    or not math.isclose(
                        float(snap["data"]["current_price"]),
                        float(source_spot), abs_tol=0.01)):
                return None
            reference = float(frame.get("reference_spx") or 0)
            if reference <= 0:
                return None
            original = (frame.get("profiles") or {}).get("0DTE", {}).get("rows") or []
            recalculated = profile(snap, day, reference)["rows"]
            if (len(recalculated) != len(original)
                    or any(
                        abs(float(a["strike_spx"]) - float(b["strike_spx"])) > 1e-6
                        or abs(float(a["net_gamma"]) - float(b["net_gamma"])) > 1e-6
                        for a, b in zip(recalculated, original))):
                return None
            # Attempt direct Edge first; a temporary engine/network failure
            # must not hide independent, already verified same-chain GEX.
            try:
                from edge_auto import result_from_snapshot
                edge = result_from_snapshot(raw, 0.0)
                direct = _clean_levels(
                    edge, day, spot=source_spot,
                    sha256=hashlib.sha256(raw).hexdigest())
                if direct:
                    return direct
            except (ValueError, KeyError, TypeError, RuntimeError,
                    OSError, OverflowError, ImportError):
                pass
            # Only the SAME official 0DTE SHA, day, clock, slot and Cboe
            # timestamp are accepted. Never use stale 17:00 levels for 07:30.
            from gex_official_csv import verified_official_gex_csv
            try:
                feed = store.download("mt4/official100.csv")
                return verified_official_gex_csv(
                    feed, raw, day, slot, cboe_stamp, source_clock)
            except (ValueError, KeyError, TypeError, RuntimeError,
                    OSError, OverflowError):
                return None
        except (ValueError, KeyError, TypeError, RuntimeError,
                OSError, OverflowError, ImportError):
            return None

    # Replay Pine uses the same validated compact/Edge pair as GEX EDGE.
    try:
        from edge_replay_data import read_pair
        compact, sidecar, report, clock = read_pair(store, week, day, slot)
        if (clock.strftime("%H:%M:%S") != source_clock
                or compact.get("cboe_timestamp_original") != cboe_stamp
                or not math.isclose(float(compact["spot_spx"]),
                                    float(source_spot), abs_tol=0.01)):
            return None
        return _clean_levels(report, day, spot=source_spot,
                             sha256=compact.get("cboe_sha256"))
    except (ValueError, KeyError, TypeError, RuntimeError,
            OSError, OverflowError, ImportError):
        return None

"""MARKET MAP — generate FOUR point-in-time Serafini Pine exports without CBOE downloads.

Read only EXISTING immutable Supabase snapshots and logs through Serafini's own
gamma_replay / pine_sources functions. Use the same exporters and news overlay
as the Dashboard. Never create historical backfills.
"""
import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from es_spx_basis import download_and_calculate
from pine_sources import load_export_profiles
from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_news_overlay import append_news

ROME = ZoneInfo("Europe/Rome")
SLOTS = (("07:30", None), ("13:30", "07:30"),
         ("15:50", "07:30"), ("20:30", "15:50"))


def basis_for_today(path, day, now):
    """Freeze first available same-day basis, never silently revise it."""
    if path.is_file():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("rome_date") != day.isoformat():
            raise ValueError("Basis artifact date mismatch")
        if not math.isfinite(float(saved["basis"])):
            raise ValueError("Stored ES-SPX basis invalid")
        return saved
    result = download_and_calculate(day)
    basis = int(Decimal(str(result["basis"])).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    saved = {
        "schema": "market_map_basis_v1",
        "rome_date": day.isoformat(),
        "basis": basis,
        "basis_unrounded": result["basis"],
        "reference_date": result["reference_date"].isoformat(),
        "source": result["source"],
        "bar_count": result["bar_count"],
        "frozen_at_rome": now.isoformat(timespec="seconds"),
        "quality": "Yahoo 15m previous cash session; not an ES CME settled spread",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return saved


def get_sources(day, store):
    iso = day.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    logs = store.list(f"{week}/logs/")
    if not logs:
        # Probe metadata only; no raw option contents, tokens, or private paths
        # are printed. A legacy anon JWT can read 0 objects due to Storage RLS.
        import base64
        import json
        api_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if api_key.startswith("sb_secret_"):
            role_kind = "MODERN_SECRET"
        elif api_key.startswith("eyJ"):
            try:
                part = api_key.split(".")[1]
                data = json.loads(base64.urlsafe_b64decode(
                    part + "=" * (-len(part) % 4)))
                role_kind = str(data.get("role") or "UNKNOWN_ROLE")
            except (ValueError, IndexError, TypeError):
                role_kind = "UNREADABLE_JWT"
        else:
            role_kind = "UNKNOWN_KEY_FORMAT"
        direct = {}
        for label, prefix in (("root", ""), ("week", week + "/"),
                              ("logs_with_slash", week + "/logs/"),
                              ("logs_without_slash", week + "/logs")):
            try:
                entries = store._list_uncached(prefix)
                direct[label] = {
                    "entries": len(entries),
                    "files": sum(bool(x.get("id")) for x in entries)}
            except Exception as exc:
                direct[label] = {"error": type(exc).__name__}
        print("STORAGE_LIST_DIAGNOSTIC " + json.dumps({
            "key_role_or_kind": role_kind,
            "project_ref_matches": store.url.rstrip("/").endswith(
                "rnpnibdxuqlmuqmcixsd.supabase.co"),
            "bucket": store.bucket, "probes": direct}, sort_keys=True))
    profiles = load_export_profiles(store, week, day, logs)
    if not profiles:
        # Diagnostic only: never log option contents, credentials, raw JSON or
        # Supabase object bodies. The reader may silently reject invalid Gamma.
        from gamma_replay import OFFICIAL, LEGACY_STUDY, weekly_expiry
        from net_gamma import read_snapshot, profile
        import json
        info = {"week": week, "logs_listed": len(logs),
                "candidate_logs": 0, "candidate_snapshots": 0,
                "rejection_causes": {}, "weekly_expiry": weekly_expiry(day).isoformat()}
        for obj in logs:
            try:
                item = json.loads(store.download(f"{week}/logs/{obj['name']}"))
                if item.get("rome_date") != day.isoformat():
                    continue
                info["candidate_logs"] += 1
                slot = next((k for k,v in (OFFICIAL | LEGACY_STUDY).items()
                             if v == item.get("scheduled_slot")), None)
                reason = None
                if not slot:
                    reason = "UNRECOGNIZED_SLOT"
                elif item.get("status") not in ("NUOVO", "INVARIATO"):
                    reason = "UNACCEPTED_STATUS"
                elif not item.get("file"):
                    reason = "MISSING_FILE_PATH"
                elif not item.get("download_rome") and not item.get("download_utc"):
                    reason = "MISSING_DOWNLOAD_CLOCK"
                else:
                    try:
                        snap = read_snapshot(store.download(item["file"]))
                        info["candidate_snapshots"] += 1
                        ref = float(snap["data"].get("prev_day_close") or 0)
                        if ref <= 0:
                            reason = "MISSING_PREVIOUS_CLOSE"
                        else:
                            one = profile(snap, day, ref)
                            weekly = profile(snap, weekly_expiry(day), ref)
                            if not one.get("rows"):
                                reason = "ZERO_0DTE_STRIKES"
                            elif not weekly.get("rows"):
                                reason = "ZERO_WEEKLY_STRIKES"
                            else:
                                reason = "READER_REJECTED_AFTER_PROFILE"
                    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
                        reason = "PROFILE_" + type(exc).__name__
                if reason:
                    info["rejection_causes"][reason] = info["rejection_causes"].get(reason, 0) + 1
            except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
                reason = "LOG_" + type(exc).__name__
                info["rejection_causes"][reason] = info["rejection_causes"].get(reason, 0) + 1
        print("SOURCE_DIAGNOSTIC " + json.dumps(info, sort_keys=True))
    return profiles


def get_news(day):
    from macro_calendar import _fetch, _prepare
    key = os.environ.get("FINANCE_CALENDAR_API_KEY", "").strip()
    if not key:
        return [], "NOT_CONFIGURED"
    try:
        return [x for x in _prepare(_fetch(day.isoformat(), day.isoformat(), key), day)
                if x["day"] == day], "VERIFIED_AT_GENERATION"
    except Exception as exc:
        return [], "UNAVAILABLE_" + type(exc).__name__


def safe_source(src, now):
    """Only frames with an independently reported download timestamp."""
    raw = src.get("download_rome")
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None or dt > now:
        return False
    if dt.astimezone(ROME).date() != now.date():
        return False
    return True


def generate(today, now, basis, sources, news, news_status, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    produced = []
    skipped = {}
    for slot, prior in SLOTS:
        src = sources.get(slot)
        older = sources.get(prior) if prior else None
        if not src or not safe_source(src, now):
            skipped[slot] = "WAIT_SOURCE_OR_TIMESTAMP"
            continue
        # Never regenerate today's earlier slots using a substantially later
        # calendar or changing external inputs. Live captures only.
        source_clock = datetime.fromisoformat(
            str(src["download_rome"]).replace("Z", "+00:00")).astimezone(ROME)
        elapsed_minutes = (now - source_clock).total_seconds() / 60.0
        if elapsed_minutes > 75:
            skipped[slot] = "WAIT_STALE_SNAPSHOT_NO_BACKFILL"
            continue
        # A missing calendar is an explicitly missing source, not permission
        # to generate a misleading "exact Dashboard" copy.
        if news_status != "VERIFIED_AT_GENERATION":
            skipped[slot] = "WAIT_NEWS_SOURCE_UNVERIFIED"
            continue
        if prior and (not older or not safe_source(older, now)):
            skipped[slot] = "WAIT_PRIOR_SOURCE"
            continue
        try:
            current = src["profiles"]
            if prior:
                pine = make_pine_comparison(
                    older["profiles"]["0DTE"], current["0DTE"],
                    current["Weekly"], basis, today.isoformat(), prior,
                    slot, src.get("cboe_timestamp") or "",
                    gex=src.get("gex_levels"))
            else:
                pine = make_pine(
                    current["0DTE"], current["Weekly"], basis,
                    today.isoformat(), src.get("cboe_timestamp") or "",
                    "Mattina 07:30", gex=src.get("gex_levels"))
            pine = append_news(pine, news, captured_at=src["download_rome"])
            data = pine.encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            stem = f"Serafini_NET_GAMMA_{today:%Y-%m-%d}_{slot.replace(':','')}_{sha[:12]}"
            output_file = output_dir / (stem + ".pine")
            output_file.write_bytes(data)
            metadata = {
                "schema": "market_map_pine_auto_v1",
                "date_rome": today.isoformat(), "slot": slot,
                "previous_slot": prior, "basis_es_spx": basis,
                "source_origin": src.get("origin"),
                "source_download_rome": src.get("download_rome"),
                "source_cboe_timestamp_unqualified": src.get("cboe_timestamp"),
                "prior_download_rome": older.get("download_rome") if older else None,
                "news_status": news_status, "news_count": len(news),
                "generated_at_rome": now.isoformat(timespec="seconds"),
                "sha256": sha, "bytes": len(data),
                "source": "Existing private Supabase snapshot; no CBOE requests",
                "quality": ("POINT_IN_TIME_NEWS_UNAVAILABLE" if news_status !=
                            "VERIFIED_AT_GENERATION" else "POINT_IN_TIME"),
            }
            (output_dir / (stem + ".metadata.json")).write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            produced.append({"slot": slot, "file": output_file.name, "sha256": sha})
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            skipped[slot] = "WAIT_INVALID_" + type(exc).__name__
    manifest = {"schema": "market_map_pine_generation_v1", "day": today.isoformat(),
                "generated_at_rome": now.isoformat(timespec="seconds"),
                "produced": produced, "skipped": skipped,
                "news_status": news_status}
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--basis-path", default="freeze/basis.json")
    parser.add_argument("--out", default="pine_daily")
    args = parser.parse_args()
    now = datetime.now(ROME)
    today = now.date()
    if today.weekday() >= 5:
        print("WAIT_WEEKEND: no retroactive generation")
        return
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    from storage import Store
    store = Store(os.environ["SUPABASE_URL"],
                  os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    sources = get_sources(today, store)
    if not sources:
        print("WAIT_NO_VERIFIED_SNAPSHOT")
        return
    # Only freeze basis after a real 07:30 profile exists. Do not construct
    # a later-day basis speculatively or backfill missing prior days.
    if "07:30" not in sources or not safe_source(sources["07:30"], now):
        print("WAIT_0730_FIRST")
        return
    basis_record = basis_for_today(Path(args.basis_path), today, now)
    news, news_status = get_news(today)
    result = generate(today, now, basis_record["basis"], sources, news,
                      news_status, Path(args.out) / today.isoformat())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

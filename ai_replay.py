"""Lettura Replay per il report AI: nessuna modifica all'archivio Cboe.

Gli snapshot ufficiali restano in report['snapshots']; i file compatti di
studio sono esportati separatamente con 50 strike per scadenza. La timeline
evita di contare due volte lo slot 13:30, se e' disponibile l'ufficiale.
"""
import hashlib
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from gamma_replay import OFFICIAL, SLOTS
from volume_audit import compare_volume_profiles
from spx_market_clock import classify_0dte_download

ROME = ZoneInfo("Europe/Rome")
FIELDS = ("call_oi", "put_oi", "call_volume", "put_volume")


def _number(value, required=False):
    if value is None or value == "":
        if required:
            raise ValueError("Valore numerico obbligatorio assente")
        return None
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("Valore numerico non finito")
    return x


def _clock(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Download privo di fuso orario")
    return dt.astimezone(ROME)


def _compact_profile(data, expiry, offset):
    if data.get("expiry") != expiry:
        raise ValueError("Scadenza Replay diversa dalla scadenza prevista")
    original = data.get("rows")
    if not isinstance(original, list) or len(original) != 50:
        raise ValueError("Replay privo di esattamente 50 strike")
    rows, seen = [], set()
    for raw in original:
        strike = _number(raw["strike_spx"], required=True)
        net = _number(raw["net_gamma"], required=True)
        if strike in seen:
            raise ValueError("Strike Replay duplicato")
        seen.add(strike)
        row = {"strike_spx": strike,
               "strike_es": None if offset is None else round(strike + offset, 6),
               "net_gamma": net}
        # Gli storici precedenti alla v2 possono NON avere OI/Volume.
        # Mai sostituire un campo assente con lo zero.
        for field in FIELDS:
            row[field] = _number(raw.get(field))
        rows.append(row)
    rows.sort(key=lambda x: x["strike_spx"])
    return {"expiry": expiry, "strike_count": 50,
            "strikes": rows, "summary": _summary(rows)}


def _summary(rows):
    positives = sorted((r for r in rows if r["net_gamma"] > 0),
                       key=lambda r: r["net_gamma"], reverse=True)
    negatives = sorted((r for r in rows if r["net_gamma"] < 0),
                       key=lambda r: r["net_gamma"])
    def brief(selected):
        return [{"strike_spx": r["strike_spx"], "strike_es": r.get("strike_es"),
                 "net_gamma": round(r["net_gamma"], 6)} for r in selected[:3]]
    complete = all(all(r.get(k) is not None for k in FIELDS) for r in rows)
    return {
        "net_total": round(sum(r["net_gamma"] for r in rows), 6),
        "positive_strikes": len(positives),
        "negative_strikes": len(negatives),
        "top_positive": brief(positives), "top_negative": brief(negatives),
        "volume_total": (round(sum(r["call_volume"] + r["put_volume"]
                                   for r in rows), 2) if complete else None),
        "oi_total": (round(sum(r["call_oi"] + r["put_oi"]
                               for r in rows), 2) if complete else None),
        "volume_oi_available": complete,
    }


def attach_replay(store, report, week, day, reference, offset):
    """Carica i Replay reali della giornata e crea una cronologia deduplicata."""
    expected = {"0DTE": day.isoformat(), "Weekly": report["expiries"]["Weekly"]}
    prefix = f"replay/{week}/"
    frames, rejected = [], []
    names = sorted(str(x.get("name", "")) for x in store.list(prefix))
    for name in names:
        if not (name.startswith(day.isoformat() + "_") and name.endswith(".json")):
            continue
        path = prefix + name
        try:
            blob = store.download(path)
            obj = json.loads(blob)
            if obj.get("schema") != "serafini_replay_v1":
                raise ValueError("Schema Replay non riconosciuto")
            if obj.get("kind") != "STUDIO_INTRADAY_NON_UFFICIALE":
                raise ValueError("Tipo Replay non riconosciuto")
            if obj.get("rome_date") != day.isoformat():
                raise ValueError("Giornata interna diversa dal file")
            slot = str(obj.get("slot_rome") or "")
            if slot not in SLOTS:
                raise ValueError("Slot Replay non riconosciuto")
            clock = _clock(obj.get("download_rome") or obj.get("download_utc"))
            if clock is None or clock.date() != day:
                raise ValueError("Orario di download assente o appartenente ad altro giorno")
            profiles = {
                label: _compact_profile(obj.get("profiles", {}).get(label) or {},
                                         expiry, offset)
                for label, expiry in expected.items()
            }
            ref = _number(obj.get("reference_spx"), required=True)
            if ref <= 0:
                raise ValueError("Riferimento SPX Replay non valido")
            frames.append({
                "source": "STUDIO_INTRADAY_NON_UFFICIALE",
                "file": path, "sha256_compact": hashlib.sha256(blob).hexdigest(),
                "slot_rome": slot, "download_rome": clock.isoformat(timespec="seconds"),
                "download_utc": obj.get("download_utc"),
                "cboe_timestamp_original": obj.get("cboe_timestamp_original"),
                "spot_spx": _number(obj.get("spot_spx")),
                "reference_spx": ref,
                "reference_matches_report": abs(ref - float(reference)) < 0.01,
                "quality": obj.get("quality") or {},
                "market_timing": classify_0dte_download(day, clock),
                "profiles": profiles,
            })
        except (ValueError, KeyError, TypeError, OverflowError,
                OSError, RuntimeError, json.JSONDecodeError) as exc:
            rejected.append({"file": path, "reason": type(exc).__name__ + ": " + str(exc)[:130]})

    # Uno slot nel report cronologico appare una sola volta.
    # L'ufficiale ha priorita' sul Replay compatto dello stesso slot.
    official_slots = {cron: slot for slot, cron in OFFICIAL.items()}
    chosen = {}
    for rec in report["snapshots"]:
        if rec.get("status") != "VALIDO":
            continue
        slot = official_slots.get(str(rec.get("acquisition_slot") or ""))
        if slot is None:
            continue
        dt = _clock(rec.get("download_utc"))
        if dt is None or dt.date() != day:
            continue
        profiles = rec.get("profiles", {})
        if not all(len(profiles.get(k, {}).get("strikes", [])) == 50
                   and profiles.get(k, {}).get("expiry") == expiry
                   for k, expiry in expected.items()):
            continue
        if slot in chosen:
            continue
        chosen[slot] = {
            "slot_rome": slot, "source": "UFFICIALE",
            "file": rec["file"], "download_rome": dt.isoformat(timespec="seconds"),
            "cboe_timestamp_original": rec.get("cboe_timestamp_original"),
            "reference_spx": float(reference),
            "market_timing": classify_0dte_download(day, dt),
            "profiles": {
                label: {"expiry": expected[label],
                        "summary": _summary(profiles[label]["strikes"]),
                        "strikes": profiles[label]["strikes"]}
                for label in expected
            },
        }

    for frame in frames:
        slot = frame["slot_rome"]
        frame["overlaps_official_slot"] = slot in chosen and chosen[slot]["source"] == "UFFICIALE"
        if slot not in chosen:
            chosen[slot] = frame

    timeline = []
    previous_timestamp = None
    for slot in SLOTS:
        item = chosen.get(slot)
        if not item:
            continue
        stamp = str(item.get("cboe_timestamp_original") or "").strip()
        repeated = bool(stamp and previous_timestamp and stamp == previous_timestamp)
        previous_timestamp = stamp or None
        timeline.append({
            "slot_rome": slot, "download_rome": item["download_rome"],
            "source": item["source"], "file": item["file"],
            "cboe_timestamp_original": item.get("cboe_timestamp_original"),
            "cboe_timestamp_repeated": repeated,
            "reference_spx": item["reference_spx"],
            "reference_matches_report": abs(item["reference_spx"] - float(reference)) < 0.01,
            "market_timing": item["market_timing"],
            "profiles": {
                label: {
                    **item["profiles"][label]["summary"],
                    "valid_for_pre_expiry_analysis": (
                        item["market_timing"]["valid_for_pre_expiry_analysis"]
                        if label == "0DTE" else True),
                    "status": (
                        item["market_timing"]["status"]
                        if label == "0DTE" else "WEEKLY_NON_IN_SCADENZA")
                } for label in expected
            },
        })

    transitions = []
    for start, end in zip(timeline, timeline[1:]):
        old = chosen[start["slot_rome"]]
        new = chosen[end["slot_rome"]]
        compatible_reference = (
            abs(old["reference_spx"] - new["reference_spx"]) < 0.01
            and abs(old["reference_spx"] - float(reference)) < 0.01)
        change = {"from_slot": start["slot_rome"], "to_slot": end["slot_rome"],
                  "reference_comparable": compatible_reference,
                  "cboe_timestamp_repeated": end["cboe_timestamp_repeated"],
                  "profiles": {}}
        if compatible_reference:
            for label in expected:
                # Il valore grezzo resta nel JSON, ma le differenze 0DTE
                # oltre la chiusura di negoziazione NY non sono operative.
                if label == "0DTE" and (
                        not start["market_timing"]["valid_for_pre_expiry_analysis"]
                        or not end["market_timing"]["valid_for_pre_expiry_analysis"]):
                    change["profiles"][label] = {
                        "status": "NON_CONFRONTABILE_POST_SCADENZA_0DTE",
                        "excluded_from_pre_expiry_analysis": True,
                        "reason": "Almeno un frame dopo le 16:00 New York",
                    }
                    continue
                older = old["profiles"][label]["strikes"]
                newer = new["profiles"][label]["strikes"]
                om = {r["strike_spx"]: r for r in older}
                nm = {r["strike_spx"]: r for r in newer}
                common = sorted(om.keys() & nm.keys())
                flips = [s for s in common if om[s]["net_gamma"] * nm[s]["net_gamma"] < 0]
                with_volume = all(all(r.get(f) is not None for f in FIELDS)
                                  for r in older + newer)
                comp = {
                    "common_strikes": len(common),
                    "changed_sign_strikes_spx": flips,
                    "net_total_delta": round(
                        new["profiles"][label]["summary"]["net_total"]
                        - old["profiles"][label]["summary"]["net_total"], 6),
                    "volume_audit": (compare_volume_profiles(
                        older, newer, old_expiry=expected[label],
                        new_expiry=expected[label]) if with_volume
                        else {"status": "VOLUME/OI NON DISPONIBILI NEL COMPACT",
                              "alert": False}),
                }
                change["profiles"][label] = comp
        transitions.append(change)

    available = [slot for slot in SLOTS if slot in chosen]
    return {
        "status": "DISPONIBILE" if available else "NESSUN REPLAY DISPONIBILE",
        "expected_slots": list(SLOTS), "available_slots": available,
        "missing_slots": [slot for slot in SLOTS if slot not in chosen],
        "official_count_in_timeline": sum(x["source"] == "UFFICIALE" for x in timeline),
        "study_count_in_timeline": sum(x["source"] != "UFFICIALE" for x in timeline),
        "study_files_total": len(frames), "rejected_files": rejected,
        "frames": frames, "timeline": timeline, "transitions": transitions,
        "post_expiry_0dte_slots": [
            item["slot_rome"] for item in timeline
            if item["market_timing"]["post_expiry"]],
        "note": ("Snapshot ufficiali in snapshots e Replay di studio in frames. "
                 "La timeline privilegia l'ufficiale negli slot sovrapposti. "
                 "Non si ricalcola il NET GAMMA; il timestamp Cboe non ha fuso dichiarato. "
                 "Volume/OI assenti nei vecchi compact restano null, mai zero."),
    }

"""Selezione dei rilevamenti reali per i cinque export Pine.

Usa ESATTAMENTE i lettori ufficiali/di studio gia' validati dal Replay.
13:30: STUDIO prima dell'11/11, UFFICIALE solo l'11/11; dal 12/11 solo Replay.
14:46: UFFICIALE dal 12/11. Non si reinterpretano i log storici.
"""
from datetime import date

from gamma_replay import _official_frames, _study_frames, weekly_expiry
from acquisition_schedule import official_export_slots, raw_study_1330_for_day


EXPORT_SLOTS = ("07:30", "13:30", "14:46", "15:50", "17:00", "20:30")


def load_export_profiles(store, week, day: date, logs):
    """Ritorna profili reali per slot, senza ripetere stime o NET GAMMA."""
    active_slots = official_export_slots(day)
    study = _study_frames(store, week, day)
    recorded = _official_frames(store, week, day, logs)
    frames = {slot: frame for slot, frame in study.items() if slot in active_slots}
    for slot, frame in recorded.items():
        if slot not in active_slots:
            continue
        # Il compact storico delle 13:30 prevale prima dell'11/11.
        if not (slot == "13:30" and raw_study_1330_for_day(day)
                and slot in frames):
            frames[slot] = frame

    expected = {"0DTE": day.isoformat(), "Weekly": weekly_expiry(day).isoformat()}
    result = {}
    for slot in active_slots:
        frame = frames.get(slot)
        if frame is None:
            continue
        profiles = frame.get("profiles") or {}
        clean = {}
        for kind in ("0DTE", "Weekly"):
            p = profiles.get(kind) or {}
            raw = p.get("rows") or []
            if p.get("expiry") not in (None, expected[kind]):
                break
            if len(raw) != 50 or len({float(r["strike_spx"]) for r in raw}) != 50:
                break
            original = [{
                "strike_spx": float(r["strike_spx"]),
                "net_gamma": float(r["net_gamma"]),
            } for r in raw]
            # L'estensione e' SOLO per il Pine: i 50 originali sono la
            # fonte autorevole per i calcoli, i livelli e il Replay.
            visual = p.get("visual_rows") or raw
            try:
                display = [{
                    "strike_spx": float(r["strike_spx"]),
                    "net_gamma": float(r["net_gamma"]),
                } for r in visual]
                original_by_strike = {r["strike_spx"]: r["net_gamma"]
                                      for r in original}
                visual_by_strike = {r["strike_spx"]: r["net_gamma"]
                                    for r in display}
                valid_visual = (
                    50 <= len(display) <= 140 and
                    len(visual_by_strike) == len(display) and
                    all(strike in visual_by_strike and
                        abs(visual_by_strike[strike] - gamma) < 0.000001
                        for strike, gamma in original_by_strike.items())
                )
            except (ValueError, TypeError, OverflowError, KeyError):
                valid_visual = False
            if not valid_visual:
                display = original
            clean[kind] = {
                "expiry": expected[kind],
                "rows": original,
                "display_rows": sorted(display, key=lambda r: r["strike_spx"]),
                "visual_coverage_complete": (
                    p.get("visual_coverage_complete") if valid_visual else None),
            }
        if len(clean) == 2:
            result[slot] = {
                "origin": frame.get("origin", "NON VERIFICATO"),
                "cboe_timestamp": frame.get("cboe_timestamp_original", ""),
                # Prefer the actual *verified* log timestamp over the
                # slot nickname: the Cboe field itself has no certified TZ.
                "download_rome": frame.get("download_rome"),
                "reference_spx": frame.get("reference_spx"),
                "profiles": clean,
            }
    return result

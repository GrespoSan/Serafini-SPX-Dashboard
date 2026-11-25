"""Visual-only extension for NDX/GLD/USO, following Serafini SPX layout.

It NEVER changes the source 0DTE/Weekly calculation, basis, ratio,
expiry, CSV original, or any input snapshot. Only uses additional REAL
strikes from the SAME already-downloaded original Cboe chain.

The original market-specific percent-band model is retained (no false
claim that ETF GLD/USO is native futures-option Gamma).
"""
import math


def expanded_display(original, calculate, reference, spot, band_pct, *,
                     strike_field, gamma_field, upper_band_limit=10.0,
                     limit=140, safety_pct=2.5):
    """Return (visual profile, metadata) with bounded real additional rows.

    The primary profile may already exceed 140: in that case preserve it
    unchanged, never truncate or fabricate strikes. UI/Pine may enforce
    separate limits later.
    """
    original_rows = original.get("rows") or []
    if not original_rows:
        return original, {"extra": 0, "reason": "Nessun profilo utilizzabile"}
    if reference <= 0 or not math.isfinite(float(reference)):
        raise ValueError("Riferimento non valido")
    base = {float(x[strike_field]): float(x[gamma_field])
            for x in original_rows}
    if len(base) != len(original_rows):
        raise ValueError("Strike duplicati nel profilo")
    try:
        spot = float(spot)
        if not math.isfinite(spot) or spot <= 0:
            spot = float(reference)
    except (ValueError, TypeError, OverflowError):
        spot = float(reference)
    requested = max(float(band_pct),
                    100 * abs(spot / reference - 1) + safety_pct)
    used_band = min(upper_band_limit, requested)
    if (used_band <= band_pct + 1e-10 or
            len(original_rows) >= limit):
        return original, {"extra": 0, "reason": "Fascia originale sufficiente"
                          if used_band <= band_pct else
                          "Numero strike originali gia' al limite",
                          "complete": used_band >= requested}
    wider = calculate(used_band)
    wider_rows = wider.get("rows") or []
    if wider.get("usable") is False or not wider_rows:
        return original, {"extra": 0, "reason": "Gamma esterni incompleti",
                          "complete": False}
    broad = {float(x[strike_field]): x for x in wider_rows}
    if any(k not in broad or
           not math.isclose(float(broad[k][gamma_field]), v,
                            rel_tol=1e-8, abs_tol=1e-8)
           for k,v in base.items()):
        return original, {"extra": 0,
                          "reason": "Fascia ampia non coerente con il nucleo",
                          "complete": False}
    minimum = min(base)
    maximum = max(base)
    lows = sorted((x for x in broad if x < minimum), reverse=True)
    highs = sorted(x for x in broad if x > maximum)
    available = limit - len(base)
    chosen = []
    for side in ((lows, highs) if spot < reference else (highs, lows)):
        chosen.extend(side[:available])
        available -= min(available, len(side))
    visual = list(original_rows) + [broad[x] for x in chosen]
    visual.sort(key=lambda row: float(row[strike_field]))
    result = dict(original)
    result["rows"] = visual
    if "selected_strikes" in result:
        result["selected_strikes"] = len(visual)
    if "complete_strikes" in result:
        result["complete_strikes"] = len(visual)
    complete = (used_band >= requested and
                (len(visual) < limit or len(visual) == len(wider_rows)))
    return result, {"extra": len(chosen), "complete": complete,
                    "band_pct": used_band, "source": "Stesso JSON Cboe"}

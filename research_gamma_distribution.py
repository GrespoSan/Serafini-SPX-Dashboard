"""Laboratorio di ricerca: variazione e concentrazione NET GAMMA Serafini.

Modulo puro, non collegato alla UI e senza accessi/scritture all'archivio.
Usa i valori net_gamma GIA' calcolati dal Replay. Non e' GEX in dollari.
"""
from __future__ import annotations

from math import isfinite


def _values(rows):
    out = {}
    for row in rows:
        try:
            strike = float(row["strike_spx"])
            gamma = float(row["net_gamma"])
        except (KeyError, TypeError, ValueError):
            continue
        if isfinite(strike) and isfinite(gamma):
            if strike in out:
                raise ValueError("Strike duplicato nel profilo")
            out[strike] = gamma
    return out


def compare_profiles(before_rows, after_rows, *, spot=None):
    """Confronta SOLO strike identici; nessun riempimento artificiale.

    Le percentuali sono normalizzate su un universo comune di strike.
    Ritorna N/D quando un denominatore e' zero o i profili non coincidono.
    """
    before, after = _values(before_rows), _values(after_rows)
    common = sorted(set(before) & set(after))
    if not common:
        return {"status": "N/D", "reason": "Nessuno strike comune", "strikes": []}
    b = {k: before[k] for k in common}
    a = {k: after[k] for k in common}
    max_b = max(abs(v) for v in b.values())
    max_a = max(abs(v) for v in a.values())
    sum_b = sum(abs(v) for v in b.values())
    sum_a = sum(abs(v) for v in a.values())
    rank_b = {k: i + 1 for i, k in enumerate(sorted(common, key=lambda k: (-abs(b[k]), k)))}
    rank_a = {k: i + 1 for i, k in enumerate(sorted(common, key=lambda k: (-abs(a[k]), k)))}

    def ratio(x, denominator):
        return 100.0 * x / denominator if denominator else None

    strikes = []
    for k in common:
        pb, pa = ratio(abs(b[k]), max_b), ratio(abs(a[k]), max_a)
        sb, sa = ratio(abs(b[k]), sum_b), ratio(abs(a[k]), sum_a)
        strikes.append({
            "strike_spx": k, "before": b[k], "after": a[k],
            "delta_absolute": a[k] - b[k],
            "relative_to_max_before_pct": pb,
            "relative_to_max_after_pct": pa,
            "relative_to_max_delta_pp": pa - pb if pa is not None and pb is not None else None,
            "share_of_abs_before_pct": sb, "share_of_abs_after_pct": sa,
            "share_delta_pp": sa - sb if sa is not None and sb is not None else None,
            "rank_before": rank_b[k], "rank_after": rank_a[k],
            "rank_improvement": rank_b[k] - rank_a[k],
            "sign_flip": b[k] * a[k] < 0,
        })

    def totals(values, predicate):
        return sum(abs(v) for k, v in values.items() if predicate(k, v))

    def change(old, new):
        return 100.0 * (new - old) / old if old > 0 else None

    def change_status(old, new):
        if old == 0:
            return "BASE ZERO · % N/D"
        if new < old:
            return "DIMINUZIONE"
        if new > old:
            return "AUMENTO"
        return "INVARIATO"

    result = {
        "status": "OK" if len(common) == len(before) == len(after) else "COPERTURA PARZIALE",
        "common_count": len(common), "before_count": len(before), "after_count": len(after),
        "common_strikes_only": True, "max_abs_before": max_b, "max_abs_after": max_a,
        "total_abs_before": sum_b, "total_abs_after": sum_a,
        "strikes": strikes,
    }
    if spot is not None:
        spot = float(spot)
        # Misura di intensita' positiva sopra spot: NON esposizione dealer osservata.
        up_b = totals(b, lambda k, v: k > spot and v > 0)
        up_a = totals(a, lambda k, v: k > spot and v > 0)
        down_b = totals(b, lambda k, v: k < spot and v < 0)
        down_a = totals(a, lambda k, v: k < spot and v < 0)
        result["fixed_spot_spx"] = spot
        result["positive_above_before"] = up_b
        result["positive_above_after"] = up_a
        result["positive_above_change_abs"] = up_a - up_b
        result["positive_above_change_pct"] = change(up_b, up_a)
        result["positive_above_status"] = change_status(up_b, up_a)
        result["negative_below_before_abs"] = down_b
        result["negative_below_after_abs"] = down_a
        result["negative_below_change_abs"] = down_a - down_b
        result["negative_below_change_pct"] = change(down_b, down_a)
        result["negative_below_status"] = change_status(down_b, down_a)
    return result

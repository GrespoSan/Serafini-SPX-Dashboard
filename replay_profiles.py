"""Selezione rigorosa dei livelli grafici Replay già archiviati da Cboe.

Il profilo originale 25+25 in rows non viene modificato: la visualizzazione
opzionale usa visual_rows del medesimo snapshot soltanto quando coerente.
"""
import math


def choose_rows(profile, extended=True):
    if not isinstance(profile, dict):
        return []
    core = profile.get("rows") or []
    extra = profile.get("visual_rows") or []
    if not extended or not extra or len(extra) < len(core) or len(extra) > 140:
        return core
    try:
        def mapping(rows):
            by_strike = {}
            for item in rows:
                strike = float(item["strike_spx"])
                value = float(item["net_gamma"])
                if not (math.isfinite(strike) and math.isfinite(value)):
                    raise ValueError("Profilo non finito")
                if strike in by_strike:
                    raise ValueError("Strike duplicato")
                by_strike[strike] = value
            return by_strike
        base = mapping(core)
        visual = mapping(extra)
        # Stessi 50 strike e valori intatti. Vietato inserire barre interpolate
        # o cambiare la formula Serafini durante la visualizzazione.
        if not all(strike in visual and math.isclose(
                value, visual[strike], abs_tol=1e-5, rel_tol=1e-9
                ) for strike, value in base.items()):
            return core
    except (KeyError, TypeError, ValueError, OverflowError):
        return core
    return extra


def union_strikes(profiles, extended=True):
    """Nessuna barra sintetica: solo coordinate presenti nei frame reali."""
    return sorted({
        float(row["strike_spx"])
        for prof in profiles
        for row in choose_rows(prof, extended)
    })

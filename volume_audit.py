"""Audit DESCRITTIVO della comparabilita' del campo Volume Cboe fra snapshot.
NON corregge, normalizza o modifica la formula NET GAMMA.
Le soglie sono filtri prudenziali del progetto, NON regole di Serafini.
"""
import math


def _nonnegative(value):
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


def compare_volume_profiles(old_rows, new_rows, old_quality=None, new_quality=None):
    """Confronto volume e OI degli stessi strike: allerta, non prova di causa.

    old_rows/new_rows: righe del profilo 0DTE o Weekly con call_volume,
    put_volume, call_oi e put_oi. Nessuna informazione futura viene utilizzata.
    """
    old = {float(r["strike_spx"]): r for r in old_rows}
    new = {float(r["strike_spx"]): r for r in new_rows}
    common = sorted(old.keys() & new.keys())
    result = {
        "status": "NON VALUTABILE",
        "common_strikes": len(common),
        "alert": False,
        "method": "Confronto dei volumi riportati sugli stessi strike; allerta euristica per calo >=70% con OI stabile entro 5%.",
        "interpretation": "Un'anomalia non prova chiusure, acquisti, vendite o reset del provider."
    }
    if len(common) < 20:
        result["reason"] = "Meno di 20 strike comuni nei due profili."
        return result

    def totals(mapping):
        v = oi = 0.0
        for strike in common:
            row = mapping[strike]
            for field, dest in (("call_volume", "v"), ("put_volume", "v"),
                                ("call_oi", "oi"), ("put_oi", "oi")):
                num = _nonnegative(row.get(field))
                if num is None:
                    return None
                if dest == "v":
                    v += num
                else:
                    oi += num
        return v, oi

    initial = totals(old)
    final = totals(new)
    if initial is None or final is None or initial[0] <= 0 or initial[1] <= 0:
        result["reason"] = "Volume iniziale/OI assente o dati numerici insufficienti."
        return result

    v0, oi0 = initial
    v1, oi1 = final
    volume_change = 100.0 * (v1 / v0 - 1)
    oi_change = 100.0 * (oi1 / oi0 - 1)
    result.update({
        "status": "NESSUNA ALLERTA DA SOGLIA",
        "volume_initial": round(v0, 2),
        "volume_final": round(v1, 2),
        "volume_change_pct": round(volume_change, 2),
        "oi_initial": round(oi0, 2),
        "oi_final": round(oi1, 2),
        "oi_change_pct": round(oi_change, 2),
    })
    if old_quality is not None and new_quality is not None:
        c0 = old_quality.get("volume")
        c1 = new_quality.get("volume")
        if isinstance(c0, int) and isinstance(c1, int):
            result["chain_contracts_volume_nonzero_initial"] = c0
            result["chain_contracts_volume_nonzero_final"] = c1
            # Non e' un conteggio di campi presenti: include solo volume NON ZERO.
            result["chain_contracts_volume_nonzero_change_pct"] = (
                round(100 * (c1 / c0 - 1), 2) if c0 else None
            )

    if volume_change <= -70.0 and abs(oi_change) <= 5.0:
        result["alert"] = True
        result["status"] = "ALLERTA COMPARABILITA VOLUME"
        result["reason"] = (
            "Il Volume riportato cala di almeno il 70% su strike comuni, "
            "mentre l'Open Interest varia al massimo del 5%. "
            "Verificare integrita' e definizione del campo Volume nel provider. "
            "Le variazioni NET GAMMA potrebbero non essere omogenee."
        )
    else:
        result["reason"] = (
            "La soglia euristica di allerta non e' scattata: "
            "non significa che la qualita' dei dati sia certificata."
        )
    return result

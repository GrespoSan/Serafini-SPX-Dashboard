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


def compare_volume_profiles(old_rows, new_rows, old_quality=None, new_quality=None, old_expiry=None, new_expiry=None):
    """Confronto volume e OI degli stessi strike: allerta, non prova di causa.

    old_rows/new_rows: righe del profilo 0DTE o Weekly con call_volume,
    put_volume, call_oi e put_oi. Nessuna informazione futura viene utilizzata.
    """
    # Non confrontare numericamente contratti di due scadenze diverse.
    if old_expiry is not None and new_expiry is not None and old_expiry != new_expiry:
        return {
            "status": "SCADENZA DIVERSA",
            "alert": False,
            "comparable": False,
            "old_expiry": old_expiry,
            "new_expiry": new_expiry,
            "reason": "Cambio scadenza Weekly: i profili non rappresentano gli stessi contratti."
        }
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


def inspect_raw_expiry(options, expiry, selected_strikes=None):
    """Verifica direttamente i campi originali Volume/OI per una scadenza Cboe.

    I contratti restano identificati dal simbolo OPRA, non solo dallo strike.
    Solo i contratti effettivamente presenti nel file sono utilizzati.
    """
    from net_gamma import parse_contract
    contracts = {}
    counts = {"contracts": 0, "volume_missing": 0, "volume_zero": 0,
              "volume_positive": 0, "volume_invalid": 0,
              "total_volume_reported": 0.0, "total_oi_reported": 0.0}
    for option in options:
        name = str(option.get("option") or "")
        parts = parse_contract(name)
        if parts is None or parts[0] != expiry:
            continue
        if selected_strikes is not None and float(parts[2]) not in selected_strikes:
            continue
        counts["contracts"] += 1
        if "volume" not in option or option.get("volume") is None or option.get("volume") == "":
            status, vol = "missing", None
        else:
            vol = _nonnegative(option.get("volume"))
            status = ("invalid" if vol is None else
                      "zero" if vol == 0 else "positive")
        counts["volume_" + status] += 1
        if vol is not None:
            counts["total_volume_reported"] += vol
        oi = _nonnegative(option.get("open_interest"))
        if oi is not None:
            counts["total_oi_reported"] += oi
        contracts[name] = {"volume": vol, "oi": oi, "status": status,
                           "strike_spx": parts[2], "side": parts[1]}
    return {"expiry": expiry.isoformat(), "counts": counts, "contracts": contracts}


def raw_transition_diagnostic(old, new, old_download_utc=None, new_download_utc=None):
    """Controllo contratto-per-contratto di reset / campi nulli / cambi scadenza.

    Una riapertura del contatore dopo l'open USA e' IPOTESI, non certezza.
    """
    if old["expiry"] != new["expiry"]:
        return {"status": "SCADENZA DIVERSA", "comparable": False,
                "old_expiry": old["expiry"], "new_expiry": new["expiry"]}
    common = sorted(set(old["contracts"]) & set(new["contracts"]))
    out = {"status": "CONFRONTABILE PER CONTRATTO", "comparable": True,
           "expiry": old["expiry"], "common_contracts": len(common),
           "old_counts": old["counts"], "new_counts": new["counts"]}
    if not common:
        out["status"] = "NESSUN CONTRATTO COMUNE"
        out["comparable"] = False
        return out
    old_sum = new_sum = old_oi = new_oi = 0.
    missing_new = zero_new = declines = increases = 0
    affected = []
    for name in common:
        p, q = old["contracts"][name], new["contracts"][name]
        pv, qv = p["volume"], q["volume"]
        if pv is None or qv is None:
            if pv is not None and qv is None:
                missing_new += 1
            continue
        old_sum += pv
        new_sum += qv
        if p["oi"] is not None and q["oi"] is not None:
            old_oi += p["oi"]
            new_oi += q["oi"]
        if pv > 0 and qv == 0:
            zero_new += 1
        if qv < pv:
            declines += 1
            affected.append({"contract": name, "old_volume": pv,
                             "new_volume": qv, "difference": qv - pv})
        elif qv > pv:
            increases += 1
    pct = 100. * (new_sum / old_sum - 1.) if old_sum else None
    oi_pct = 100. * (new_oi / old_oi - 1.) if old_oi else None
    affected.sort(key=lambda x: x["difference"])
    out.update({
        "volume_previous": round(old_sum, 2),
        "volume_current": round(new_sum, 2),
        "volume_change_pct": round(pct, 2) if pct is not None else None,
        "oi_change_pct": round(oi_pct, 2) if oi_pct is not None else None,
        "contracts_volume_lower": declines,
        "contracts_volume_higher": increases,
        "contracts_positive_to_zero": zero_new,
        "contracts_become_missing": missing_new,
        "example_largest_declines": affected[:5],
        "interpretation": "La riduzione del volume non prova chiusure di posizioni."
    })

    # Controllo in fuso US/New York. NON convertiamo il timestamp Cboe privo
    # di indicazione di timezone: utilizziamo i download UTC registrati.
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    def ny(utc_string):
        try:
            stamp = datetime.fromisoformat(str(utc_string).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
            return stamp.astimezone(ZoneInfo("America/New_York"))
        except (ValueError, TypeError, OverflowError):
            return None
    t0, t1 = ny(old_download_utc), ny(new_download_utc)
    crossed_open = bool(t0 and t1 and t0.date() == t1.date()
                        and t0.time() < time(9, 30) <= t1.time())
    out["crossed_us_cash_open"] = crossed_open
    out["possible_new_session_volume_reset"] = bool(
        crossed_open and pct is not None and pct <= -70.
        and oi_pct is not None and abs(oi_pct) <= 5.
        and declines > 0)
    if out["possible_new_session_volume_reset"]:
        out["status"] = "POSSIBILE RESET VOLUME ALL'APERTURA USA - DA VERIFICARE"
    return out

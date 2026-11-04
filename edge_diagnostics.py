"""Diagnostica opzionale Edge: GEX per scadenza, NET/Gross e DEX.

Solo lettura dei contratti gia' finalizzati dal motore Edge originale.
Non usa il motore NET GAMMA Serafini, non salva dati e non modifica i wall.
DEX = delta Black-Scholes * OI * moltiplicatore * spot, convenzione
long option: call positiva e put negativa. Non rappresenta un book dealer.
"""
from __future__ import annotations

from collections import defaultdict
import math


def net_gross_pct(net: float, gross: float) -> float | None:
    """NET/Gross in %, with signed NET and absolute Gross."""
    net, gross = float(net), float(gross)
    if not math.isfinite(net) or not math.isfinite(gross) or gross < 0:
        raise ValueError("NET/Gross GEX non finiti o Gross negativo.")
    if gross == 0:
        if abs(net) > .01:
            raise ValueError("NET GEX diverso da zero con Gross nullo.")
        return None
    if abs(net) > gross + max(.01, 1e-10 * gross):
        raise ValueError("NET GEX incompatibile con Gross GEX.")
    return 100.0 * net / gross


def spot_freshness_label(basis: str | None) -> str:
    """The payload does not have an independently timestamped spot quote."""
    if basis == "regular_close":
        return ("Spot Cboe trattato dal motore Edge come ultima chiusura "
                "regolare, NON come quotazione live. L'orario del payload "
                "non dimostra la freschezza dello spot.")
    if basis == "payload":
        return ("Spot Cboe presente nella fotografia; Cboe non fornisce "
                "qui un timestamp indipendente del prezzo. Freschezza "
                "dello spot NON verificabile dal solo JSON.")
    return ("Origine temporale dello spot non verificabile: la fotografia "
            "Cboe non prova che il prezzo sia aggiornato.")


def summarize_exposures(contracts, spot: float, delta_fn):
    """Group the complete set of VALID contracts without clipping strike bands.

    delta_fn receives (spot, strike, T, rate, iv, option_type).
    r=q=0 matches the pinned Edge Black-Scholes approximation.
    """
    spot = float(spot)
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("Spot non valido per i diagnostici Edge.")
    expiries = defaultdict(lambda: {"call_gex": [], "put_gex": [],
                                     "call_dex": [], "put_dex": [], "n": 0})
    strikes = defaultdict(lambda: {"call_dex": [], "put_dex": []})
    for c in contracts:
        if not c.valid:
            continue
        if c.type not in ("call", "put"):
            raise ValueError("Tipo di opzione non supportato per DEX.")
        if not c.T or c.T <= 0 or c.iv is None or c.iv <= 0:
            raise ValueError("Contratto Edge valido privo di T o IV.")
        delta = float(delta_fn(spot, c.strike, c.T, 0.0, c.iv, c.type))
        dex = delta * float(c.oi) * float(c.multiplier) * spot
        gex = float(c.gex)
        if not all(math.isfinite(x) for x in (delta, dex, gex)):
            raise ValueError("DEX/GEX contiene valori non finiti.")
        if c.type == "call" and (delta < -1e-12 or gex < -1e-8):
            raise ValueError("Segno call incoerente.")
        if c.type == "put" and (delta > 1e-12 or gex > 1e-8):
            raise ValueError("Segno put incoerente.")
        day = c.expiry.isoformat()
        item = expiries[day]
        item[c.type + "_gex"].append(gex)
        item[c.type + "_dex"].append(dex)
        item["n"] += 1
        strikes[float(c.strike)][c.type + "_dex"].append(dex)

    if not expiries:
        raise ValueError("Nessun contratto Edge valido per la diagnostica.")
    expiry_rows = []
    for day, group in sorted(expiries.items()):
        cg = math.fsum(group["call_gex"])
        pg = math.fsum(group["put_gex"])
        cd = math.fsum(group["call_dex"])
        pd = math.fsum(group["put_dex"])
        ng, gg = cg + pg, cg - pg
        expiry_rows.append({
            "expiry": day, "call_gex": cg, "put_gex": pg,
            "net_gex": ng, "gross_gex": gg,
            "net_gross_pct": net_gross_pct(ng, gg),
            "call_dex": cd, "put_dex": pd,
            "net_dex": cd + pd, "gross_dex": cd - pd,
            "valid_contracts": group["n"],
        })
    strike_rows = []
    for strike, group in sorted(strikes.items()):
        cd = math.fsum(group["call_dex"])
        pd = math.fsum(group["put_dex"])
        strike_rows.append({
            "strike_spx": strike, "call_dex": cd,
            "put_dex": pd, "net_dex": cd + pd,
        })
    calls = math.fsum(r["call_dex"] for r in expiry_rows)
    puts = math.fsum(r["put_dex"] for r in expiry_rows)
    return {
        "expiries": expiry_rows, "dex_rows": strike_rows,
        "call_dex": calls, "put_dex": puts, "net_dex": calls + puts,
        "gross_dex": calls - puts,
    }

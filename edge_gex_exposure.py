"""Full-chain signed 0DTE Edge GEX components, not a plotted strike sample.

The pinned Edge engine computes totals across ALL valid selected-expiry
contracts, while its visible bar profile is clipped to +/-10% of spot.
Never sum the displayed bars to infer call/put totals.

Calls-positive / puts-negative is a dealer-positioning MODEL assumption,
NOT observed positions and NOT a direction signal.
"""
import math


def full_chain_gex_components(contracts, net_gex, gross_gex):
    """Reconcile signed call/put components against the pinned Edge engine.

    Returns USD dollar delta exposure change for a 1% move in the underlying.
    Fail closed on inconsistent sums instead of silently showing mismatched
    components in the Serafini SPX 0DTE panel.
    """
    values = {"call": [], "put": []}
    for contract in contracts:
        if not contract.valid:
            continue
        kind = contract.type
        if kind not in values:
            raise ValueError("Edge GEX: tipologia contratto non riconosciuta.")
        val = float(contract.gex)
        if not math.isfinite(val):
            raise ValueError("Edge GEX: contributo non finito.")
        if kind == "call" and val < 0 or kind == "put" and val > 0:
            raise ValueError("Edge GEX: segno Call/Put incoerente.")
        values[kind].append(val)

    calls = math.fsum(values["call"])
    puts = math.fsum(values["put"])
    total = float(net_gex)
    gross = float(gross_gex)
    if not all(math.isfinite(x) for x in (calls, puts, total, gross)):
        raise ValueError("Edge GEX: aggregati non finiti.")
    if (not math.isclose(calls + puts, total, rel_tol=1e-10, abs_tol=0.01)
            or not math.isclose(calls - puts, gross, rel_tol=1e-10, abs_tol=0.01)):
        raise ValueError("Edge GEX: NET/Gross e Call/Put non riconciliati.")
    return {
        "call_gex": calls,
        "put_gex": puts,
        "modeled_gamma_sign": ("Negative" if total < 0 else
                               "Positive" if total > 0 else "Zero"),
    }


def format_gex_billions(value, *, signed=False):
    """Locale-independent compact number, as billions USD per 1% move."""
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError("Edge GEX: importo non finito.")
    prefix = "+" if signed and amount > 0 else ""
    return f"{prefix}{amount / 1_000_000_000:,.2f} mld USD"

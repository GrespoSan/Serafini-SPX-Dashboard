"""Verified Weekly selection for RUT/RUTW and DJX/DJXW.

Never infer the existence of a Weekly from the weekday alone: actual Cboe
option-chain contracts and a usable Call/Put Gamma profile are required.
"""
from datetime import date
from manual_csv.mercati.v2_universal_cboe import profile


def weekly_candidates(expirations, primary):
    """Only actual Friday expirations strictly later than the primary expiry."""
    if not isinstance(primary, date):
        raise ValueError("Scadenza principale non valida")
    return sorted(d for d in set(expirations) if d > primary and d.weekday() == 4)


def choose_weekly(doc, ticker, primary, reference, band, expirations):
    """Return earliest real usable Friday Weekly, diagnostics for every candidate.

    The first calendar Friday is not automatically suitable: it may lack a
    complete valid Call/Put chain. No fallback to made-up contracts.
    """
    diagnostics = {}
    choice = None
    chosen_profile = None
    for expiry in weekly_candidates(expirations, primary):
        try:
            result = profile(doc, ticker, expiry, reference, band)
            diagnostics[expiry] = result
            if choice is None and result.get("usable") and result.get("rows"):
                choice, chosen_profile = expiry, result
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            diagnostics[expiry] = {"usable": False, "rows": [],
                                   "reason": str(exc)[:180]}
    return choice, chosen_profile, diagnostics

"""Validated read-only 0DTE Edge GEX for the SPX/ES Trend Daily header.

Do not compute new Gamma, download a second Cboe file, or use a cached
value from another Streamlit run. Reuse ONLY the already validated Edge
report for the same Serafini Cboe snapshot in the current render pass.
"""
from __future__ import annotations

from datetime import date
import math
import re


def prepare_daily_gex(edge, expected_day: date, download_clock=None):
    """Return two existing metrics only for a consistent 0DTE Edge report.

    Called AFTER the Edge panel has confirmed its Cboe SHA-256 matches
    the Serafini snapshot and its ES offset/expiry agree. Fail closed:
    this helper never reconstructs NET GEX from the chart's strike bars.
    """
    if not isinstance(edge, dict) or not isinstance(expected_day, date):
        raise ValueError("Fotografia Edge non verificata.")
    day = expected_day.isoformat()
    if edge.get("symbol") != "SPX" or edge.get("mode") != "0DTE":
        raise ValueError("Il modello non è SPX 0DTE.")
    if edge.get("expiries") != [day]:
        raise ValueError("Scadenza 0DTE differente dal rilevamento.")
    digest = edge.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise ValueError("Impronta Cboe non verificabile.")
    sign = edge.get("modeled_gamma_sign")
    if sign not in ("Positive", "Negative", "Zero"):
        raise ValueError("Segno Gamma modellato non valido.")
    values = (edge.get("net_gex"), edge.get("call_gex"), edge.get("put_gex"))
    if (any(isinstance(n, bool) or not isinstance(n, (float, int))
            or not math.isfinite(n) for n in values)):
        raise ValueError("GEX della fotografia non validi.")
    net, calls, puts = (float(n) for n in values)
    if (calls < 0 or puts > 0
            or not math.isclose(math.fsum((calls, puts)), net,
                                rel_tol=1e-10, abs_tol=.01)):
        raise ValueError("NET GEX e Call/Put non riconciliati.")
    if (net > 0 and sign != "Positive") or (net < 0 and sign != "Negative") or (
            net == 0 and sign != "Zero"):
        raise ValueError("Il segno Gamma differisce dal NET GEX.")
    if download_clock is not None and not isinstance(download_clock, str):
        raise ValueError("Ora del download Cboe non valida.")
    return {
        "modeled_gamma_sign": sign,
        "net_gex": net,
        "sha256": digest,
        "expiry": day,
        "download_clock": download_clock,
    }

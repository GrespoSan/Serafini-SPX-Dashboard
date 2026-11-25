"""Visual-only axes for native ETF NET GAMMA and optional future projection.

A missing or invalid synchronized price mapping can never hide usable
GLD/USO Cboe gamma: the fallback uses the ETF's REAL option strikes.
The gamma X values and Cboe row dictionaries are never transformed.
The conversion must still be verified independently by the existing
Pine exporter before showing GC/MGC or CL/MCL levels.
"""
from __future__ import annotations
import math


FUTURES = {"GLD": "GC", "USO": "CL"}


def chart_price_axes(symbol, mapping, y_limits, etf_ticks):
    """Return chart-only price transformation; never change Gamma or exports."""
    if symbol not in FUTURES:
        raise ValueError("Sottostante ETF non riconosciuto.")
    if len(y_limits) != 2:
        raise ValueError("Intervallo strike ETF non valido.")
    ymin, ymax = map(float, y_limits)
    ticks = [float(value) for value in etf_ticks]
    if not all(math.isfinite(v) for v in [ymin, ymax, *ticks]) or ymin >= ymax:
        raise ValueError("Strike ETF non validi.")

    projected = False
    ratio = 1.0
    if isinstance(mapping, dict) and mapping.get("etf_symbol") == symbol:
        try:
            value = float(mapping.get("ratio"))
            etf = float(mapping.get("etf_price"))
            future = float(mapping.get("future_price"))
            contract = str(mapping.get("future_tv_symbol") or "")
            if (math.isfinite(value) and 0 < value < 1000
                    and math.isfinite(etf) and etf > 0
                    and math.isfinite(future) and future > 0
                    and math.isclose(value, future / etf, rel_tol=1e-6)
                    and FUTURES[symbol] in contract.split(":")[-1][:3]):
                projected, ratio = True, value
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            pass

    return {
        "projected": projected,
        "price_ratio": ratio,
        "axis_symbol": FUTURES[symbol] if projected else symbol,
        "y_range": [ymin * ratio, ymax * ratio],
        "tick_levels": [price * ratio for price in ticks],
    }

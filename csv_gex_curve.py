"""Pure global extrema for the already calculated 201-point GEX curve."""
from __future__ import annotations

import math

from csv_gex_price_display import gex_display_price


def curve_extrema(report, *, symbol, es_spx_basis=None):
    """Global extrema within ±10% theoretical scenario; NOT strike extrema."""
    curve=report.get("curve") or []
    values=[]
    for row in curve:
        try:
            price=gex_display_price(row["spot"],symbol,es_spx_basis)
            gex=float(row["net_gex"])
            if price is None or not math.isfinite(gex):
                return None
            values.append((price,gex))
        except (ValueError,TypeError,KeyError,OverflowError):
            return None
    if len(values)<2:
        return None
    center=values[len(values)//2][0]
    high=max(values,key=lambda p:(p[1],-abs(p[0]-center)))
    low=min(values,key=lambda p:(p[1],abs(p[0]-center)))
    return {"maximum":{"price":high[0],"gex":high[1]},
            "minimum":{"price":low[0],"gex":low[1]}}


def quote_inside_curve(report, quote, *, symbol, es_spx_basis=None):
    """Avoid stretching a historical scenario to an unrelated modern ES quote."""
    if symbol!="SPX" or not isinstance(quote,dict) or quote.get("price") is None:
        return False
    try:
        price=float(quote["price"])
        if not math.isfinite(price) or price<=0:
            return False
        values=[gex_display_price(row["spot"],symbol,es_spx_basis)
                for row in (report.get("curve") or [])]
        if len(values)<2 or any(v is None for v in values):
            return False
        return min(values)<=price<=max(values)
    except (TypeError,ValueError,KeyError,OverflowError):
        return False

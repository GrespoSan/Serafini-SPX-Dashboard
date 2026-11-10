"""Replay SPX: SOLO visualizzazione, fascia strike e zoom stabile.

Mai ricalcolare il NET GAMMA; profilo originale Serafini 25+25
sempre separato dai livelli grafici aggiuntivi della stessa chain Cboe.
"""
import math

from replay_profiles import choose_rows

POOL_LIMIT = 600
MIN_BAND_PCT = 0.5
MAX_BAND_PCT = 10.0
DEFAULT_BAND_PCT = 3.0
FOCUS_EXTRA_PCT = 0.6


def _valid_pool(profile):
    """Accetta il pool archiviato solo se contiene i 50 originali identici."""
    core = profile.get("rows") or []
    pool = profile.get("visual_pool_rows") or []
    if not core or not pool or len(pool) > POOL_LIMIT:
        return None
    try:
        def index(rows):
            out = {}
            for item in rows:
                strike, net = float(item["strike_spx"]), float(item["net_gamma"])
                if not (math.isfinite(strike) and strike > 0 and math.isfinite(net)):
                    raise ValueError("Dati strike non validi")
                if strike in out:
                    raise ValueError("Strike duplicato")
                out[strike] = net
            return out
        base, extended = index(core), index(pool)
        if not all(k in extended and math.isclose(v, extended[k],
                        abs_tol=1e-5, rel_tol=1e-9) for k, v in base.items()):
            return None
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    return pool


def plotted_rows(profile, reference, spot, band_pct=DEFAULT_BAND_PCT,
                 automatic=True, extended=True):
    """Filtra soltanto strike reali della fotografia.

    Le fotografie storiche senza visual_pool_rows sono limitate a visual_rows.
    La fascia e il focus sullo spot si applicano SOLO al grafico, non al core.
    """
    if not isinstance(profile, dict):
        return []
    if not extended:
        return choose_rows(profile, False)
    reference = float(reference)
    band_pct = float(band_pct)
    if not (math.isfinite(reference) and reference > 0 and
            math.isfinite(band_pct) and MIN_BAND_PCT <= band_pct <= MAX_BAND_PCT):
        raise ValueError("Fascia strike Replay non valida")
    rows = _valid_pool(profile)
    if rows is None:
        rows = choose_rows(profile, True)
    low = reference * (1.0 - band_pct / 100.0)
    high = reference * (1.0 + band_pct / 100.0)
    if automatic:
        try:
            point = float(spot)
            if math.isfinite(point) and point > 0:
                pad = reference * FOCUS_EXTRA_PCT / 100.0
                low, high = min(low, point - pad), max(high, point + pad)
        except (ValueError, TypeError, OverflowError):
            pass
    return sorted(
        (row for row in rows if low <= float(row["strike_spx"]) <= high),
        key=lambda row: float(row["strike_spx"]),
    )


def price_zoom_bounds(spots, low_prices, high_prices, visible_levels,
                      reference, complete=False):
    """Stessa scala verticale in TUTTI i frame (Plotly e GIF).

    Automatico: segue le escursioni osservate dei prezzi (non l'intero pool).
    Completo: include tutti gli strike realmente selezionati dalla fascia.
    Non vengono generati livelli sintetici: sono solo limiti di visualizzazione.
    """
    reference = float(reference)
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("Close precedente non valido")
    observed = []
    for group in (spots, low_prices, high_prices):
        for v in group:
            if v is None:
                continue
            x = float(v)
            if math.isfinite(x) and x > 0:
                observed.append(x)
    active = list(observed)
    if complete or not active:
        active.extend(float(v) for v in visible_levels
                      if v is not None and math.isfinite(float(v)) and float(v) > 0)
    if not active:
        raise ValueError("Prezzi e strike non disponibili per la scala")
    lo, hi = min(active), max(active)
    span = hi - lo
    # Minimo 0,4% del Close per evitare una vista eccessivamente stretta;
    # minimo 12 pt + 12% della seduta come spazio per barre e annotazioni.
    margin = max(12.0, span * 0.12, reference * 0.004)
    center = (lo + hi) / 2
    half = max(span / 2 + margin, reference * 0.005)
    return [center - half, center + half]


def coverage_note(profile, reference, band_pct, automatic, spot):
    """Descrive la copertura realmente archiviata, senza promettere nuovi strike."""
    rows = _valid_pool(profile)
    if rows is None:
        rows = choose_rows(profile, True)
        origin = "profilo storico limitato"
    else:
        origin = "pool Cboe esteso"
    if not rows:
        return {"origin": origin, "count": 0, "capped": False}
    selected = plotted_rows(profile, reference, spot, band_pct, automatic, True)
    return {
        "origin": origin,
        "count": len(selected),
        "total_archived": len(rows),
        "low": min(float(r["strike_spx"]) for r in rows),
        "high": max(float(r["strike_spx"]) for r in rows),
        "capped": bool(profile.get("visual_pool_truncated")) if
                  origin == "pool Cboe esteso" else False,
    }

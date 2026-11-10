"""Outcome ES osservazionali per il laboratorio gamma; nessun backtest trade.

L'ingresso teorico di riferimento e' il primo CLOSE 5m successivo al
download + 15 minuti di embargo conservativo (ritardo Cboe non certificato).
Non usare prezzi prima della disponibilita' della fotografia.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def _download(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Download senza timezone")
    return stamp.astimezone(ROME)


def forward_outcomes(candles, download_rome, horizons=(15, 30, 60), embargo_minutes=15):
    """Returns N/D on gaps/missing horizons; NEVER interpolates prices."""
    stamp = _download(download_rome)
    if embargo_minutes < 0:
        raise ValueError("Embargo negativo")
    earliest = stamp + timedelta(minutes=embargo_minutes)
    bars = candles.sort_values("bar_close")
    eligible = bars.loc[bars["bar_close"] > earliest]
    if eligible.empty:
        return {"status": "N/D", "reason": "Nessuna barra post-embargo"}
    first = eligible.iloc[0]
    entry_time = first["bar_close"].to_pydatetime()
    # Reject delayed reference candles: only the next regular 5m close.
    if (entry_time - earliest).total_seconds() > 5 * 60 + 1:
        return {"status": "N/D", "reason": "Lacuna prezzo alla partenza"}
    base = float(first["close"])
    result = {"status": "OK", "reference_close": base,
              "reference_time": entry_time.isoformat(),
              "embargo_minutes": embargo_minutes}
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("Orizzonte non positivo")
        target = entry_time + timedelta(minutes=horizon)
        found = bars.loc[bars["bar_close"] == target]
        if len(found) != 1:
            result[f"es_{horizon}m_pts"] = None
            result[f"es_{horizon}m_pct"] = None
        else:
            end = float(found.iloc[0]["close"])
            result[f"es_{horizon}m_pts"] = end - base
            result[f"es_{horizon}m_pct"] = 100 * (end / base - 1) if base > 0 else None
    return result

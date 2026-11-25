"""Verified, aligned index/futures basis for RUT/RTY and DJX/YM.

Uses a dated quarterly contract (CME rollover convention), Massive 15m futures
and Yahoo 15m cash index. No fallback to asynchronous prices. Mean is calculated
on the most recent common completed cash session, like Nasdaq's basis engine.
DJX quotes are 1/100 of DJIA; Yahoo ^DJI is divided by 100 only for matching
DJX scaling, and the resulting price basis is expressed in YM index points.
"""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
from manual_csv.mercati.ndx_basis import nq_contract_for_date, _dated_nq_bars, _close

NY = ZoneInfo("America/New_York")
CONFIG = {
    "RUT": {"future": "RTY", "index": "^RUT", "factor": 1.0},
    "DJX": {"future": "YM", "index": "^DJI", "factor": 100.0},
}


def dated_contract(symbol: str, day: date) -> dict:
    if symbol not in CONFIG:
        raise ValueError("Indice non supportato")
    original = nq_contract_for_date(day)
    root = CONFIG[symbol]["future"]
    return {
        **original,
        "ticker": original["ticker"].replace("NQ", root, 1),
        "tv_symbol": original["tv_symbol"].replace("NQ", root, 1),
    }


def compute_basis(symbol: str, day: date, massive_key: str) -> dict:
    """Return basis in futures points, from a common completed 15m cash day."""
    if symbol not in CONFIG:
        raise ValueError("Indice non supportato")
    today = datetime.now(NY).date()
    if day < today - timedelta(days=50) or day > today + timedelta(days=1):
        raise ValueError("Data fuori dalla finestra intraday verificabile")
    if not massive_key:
        raise ValueError("MASSIVE_API_KEY assente: serve un differenziale verificato")
    info = CONFIG[symbol]
    contract = dated_contract(symbol, day)
    start = (day - timedelta(days=10)).isoformat()
    end = (min(today + timedelta(days=1), day + timedelta(days=1))).isoformat()
    frame = yf.download(info["index"], start=start, end=end, interval="15m",
                        auto_adjust=False, prepost=False, progress=False, threads=False)
    cash = _close(frame, info["index"])
    if cash.empty or cash.index.tz is None:
        raise ValueError("Yahoo: indice cash intraday non disponibile")
    cash = cash.copy()
    cash.index = cash.index.tz_convert(NY)
    good = cash[(cash.index.time >= time(9, 30)) &
                (cash.index.time < time(16, 0)) &
                (cash.index.date < day)]
    available = sorted(set(good.index.date))
    if not available:
        raise ValueError("Nessuna seduta cash precedente verificabile")
    previous = available[-1]
    futures = _dated_nq_bars(contract["ticker"], previous, massive_key)
    futures.index = futures.index.tz_convert(NY)
    cash_scaled = cash * (1.0 if symbol == "RUT" else 1.0)
    left = pd.to_numeric(futures, errors="coerce").rename("future")
    right = pd.to_numeric(cash_scaled, errors="coerce").rename("cash")
    paired = pd.concat([left, right], axis=1).dropna().sort_index()
    paired = paired[~paired.index.duplicated(keep="last")]
    paired = paired[(paired.index.date == previous) &
                    (paired.index.time >= time(9, 30)) &
                    (paired.index.time < time(16, 0))]
    if len(paired) < 20 or paired.index[0].time() > time(9, 45) or paired.index[-1].time() < time(15, 30):
        raise ValueError(f"Seduta {previous}: insufficienti barre 15m sincronizzate ({len(paired)})")
    differences = paired["future"] - paired["cash"]
    if not all(pd.notna(differences)):
        raise ValueError("Differenziale non finito")
    return {
        "basis": float(differences.mean()),
        "minimum": float(differences.min()),
        "maximum": float(differences.max()),
        "bar_count": len(paired),
        "reference_date": previous,
        "ticker": contract["ticker"],
        "tv_contract": contract["tv_symbol"],
        "roll_day": contract["roll_day"],
        "source": f"Massive {contract['ticker']} + Yahoo {info['index']} · 15m cash sincronizzati",
        "method": "media delle differenze su barre chiuse comuni",
        "factor": info["factor"],
    }

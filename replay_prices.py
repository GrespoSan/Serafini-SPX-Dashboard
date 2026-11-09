"""Candele futures ES per Replay: Massive 5m se completo; fallback Yahoo verificato.

- Il Net Gamma Cboe resta completamente separato.
- Non si fondono barre di due provider nello stesso grafico.
- Barre Yahoo con grave salto di apertura DOPO una lacuna temporale vengono
  escluse (non inventate, interpolate o sostituite con prezzi non verificati).
- Timestamp aware Europe/Rome e filtro solo barre CHIUSE entro lo snapshot.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc


def _day_window(day):
    if not isinstance(day, date):
        raise ValueError("Giornata Replay non valida.")
    start = datetime.combine(day, time.min, tzinfo=ROME)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=ROME)
    return start, end


def _validated_candles(frame, day):
    """Controllo di forma e integrità OHLC comune a entrambi i feed."""
    start, end = _day_window(day)
    if frame.empty or "bar_close" not in frame:
        raise ValueError("OHLC ES assenti.")
    out = frame.copy()
    out["bar_close"] = pd.to_datetime(out["bar_close"])
    if out["bar_close"].dt.tz is None:
        raise ValueError("Timestamp candele senza fuso.")
    out["bar_close"] = out["bar_close"].dt.tz_convert(ROME)
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.loc[
        (out["bar_close"] >= start) &
        (out["bar_close"] < end) &
        (out["bar_close"] <= datetime.now(UTC).astimezone(ROME))
    ].dropna(subset=["open", "high", "low", "close"])
    out = out.loc[
        (out[["open", "high", "low", "close"]] > 0).all(axis=1) &
        (out["high"] >= out[["open", "close", "low"]].max(axis=1)) &
        (out["low"] <= out[["open", "close", "high"]].min(axis=1))
    ]
    out = out.drop_duplicates(subset=["bar_close"], keep=False).sort_values("bar_close")
    if out.empty:
        raise ValueError("Nessuna candela ES valida.")
    return out.reset_index(drop=True)


def remove_suspect_gap_bars(frame):
    """Esclude solo evidenti buchi+anomalie, senza consultare barre FUTURE.

    Caso osservato: Yahoo 09/11/2026 11:50 Roma, dopo una barra 11:45
    mancante, Open -34.75 dal precedente Close e chiusura quasi invariata.
    L'identificazione è generale, puramente sui dati già chiusi, non legata
    a uno specifico orario o al prezzo della giornata.
    """
    keep = [True] * len(frame)
    warnings = []
    for i in range(1, len(frame)):
        previous, current = frame.iloc[i - 1], frame.iloc[i]
        gap = (current["bar_close"] - previous["bar_close"]).total_seconds() / 60
        open_jump = abs(float(current["open"]) - float(previous["close"]))
        close_distance = abs(float(current["close"]) - float(previous["close"]))
        bar_range = float(current["high"]) - float(current["low"])
        if (5.1 < gap <= 30 and open_jump >= 15.0 and
                close_distance <= 6.0 and bar_range >= open_jump * 0.8):
            keep[i] = False
            warnings.append(current["bar_close"].strftime("%H:%M"))
    result = frame.loc[keep].reset_index(drop=True)
    if result.empty:
        raise ValueError("Tutte le candele Yahoo sospette: grafico non disponibile.")
    return result, warnings


@st.cache_data(ttl=240, show_spinner=False, max_entries=12)
def _yahoo_candles(day):
    start, end = _day_window(day)
    if (datetime.now(UTC) - start.astimezone(UTC)).days > 59:
        raise ValueError("Yahoo 5m non conserva normalmente questa giornata (>59 giorni)")
    raw = yf.download(
        "ES=F", start=(day - timedelta(days=1)).isoformat(),
        end=(day + timedelta(days=2)).isoformat(),
        interval="5m", auto_adjust=False, progress=False,
        prepost=True, threads=False,
    )
    if raw is None or raw.empty:
        raise ValueError("Candele ES=F 5m non disponibili da Yahoo")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if any(k not in raw for k in ("Open", "High", "Low", "Close")):
        raise ValueError("OHLC mancanti da Yahoo.")
    if raw.index.tz is None:
        raise ValueError("Yahoo senza fuso orario: non utilizzabile.")
    # Yahoo indicizza per apertura: convertire a CHIUSURA per il cutoff.
    frame = pd.DataFrame({
        "bar_close": raw.index.tz_convert(ROME) + pd.Timedelta(minutes=5),
        "open": raw["Open"].to_numpy(),
        "high": raw["High"].to_numpy(),
        "low": raw["Low"].to_numpy(),
        "close": raw["Close"].to_numpy(),
    })
    cleaned, exclusions = remove_suspect_gap_bars(_validated_candles(frame, day))
    cleaned.attrs["candle_source"] = "Yahoo ES=F · 5m verificato"
    cleaned.attrs["excluded"] = exclusions
    return cleaned


@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def _massive_candles(day, contract, _api_key):
    """Contratto reale, endpoint futures; cache breve, non usare cache Daily."""
    import re
    if not re.fullmatch(r"ES[FGHJKMNQUVXZ][0-9]{1,2}", str(contract)):
        raise ValueError("Contratto futures Massive non valido (es. ESZ6).")
    url = f"https://api.massive.com/futures/v1/aggs/{contract}"
    start, end = _day_window(day)
    params = {
        "resolution": "5min",
        "window_start.gte": (day - timedelta(days=1)).isoformat(),
        "window_start.lt": (day + timedelta(days=1)).isoformat(),
        "sort": "window_start.asc", "limit": 1000,
    }
    response = requests.get(
        url, params=params,
        headers={"Authorization": f"Bearer {_api_key}", "Accept": "application/json"},
        timeout=(6, 18),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") not in ("OK", "DELAYED"):
        raise ValueError("Massive Futures: risposta non valida.")
    if payload.get("next_url"):
        raise ValueError("Massive Futures: pagina parziale, rifiutata.")
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Massive Futures: nessuna candela.")
    records = []
    for row in rows:
        if row.get("ticker", contract) != contract:
            continue
        try:
            end_stamp = pd.Timestamp(int(row["window_start"]), unit="ns", tz=UTC)
            records.append({
                "bar_close": end_stamp.tz_convert(ROME) + pd.Timedelta(minutes=5),
                "open": row["open"], "high": row["high"],
                "low": row["low"], "close": row["close"],
            })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    frame = _validated_candles(pd.DataFrame(records), day)
    frame.attrs["candle_source"] = f"Massive Futures {contract} · CME 5m"
    frame.attrs["excluded"] = []
    return frame


def massive_covers_requested_interval(frame, snapshot_at):
    """Non mostrare un feed parziale come fosse la sessione completa.

    Richiede copertura fino all'ultima candela conclusa prima dello snapshot;
    nessun buco superiore a 5m fra le 01:00 e lo snapshot, evitando false
    conferme a un feed che termina molte ore prima.
    """
    if frame.empty or snapshot_at is None or snapshot_at.tzinfo is None:
        return False
    local = snapshot_at.astimezone(ROME)
    limit = local.replace(minute=(local.minute // 5) * 5, second=0, microsecond=0)
    relevant = frame.loc[frame["bar_close"] <= limit].sort_values("bar_close")
    if relevant.empty:
        return False
    if relevant["bar_close"].max() < limit:
        return False
    # RTH e premercato devono essere integri; non basta una singola barra recente.
    stable = relevant.loc[relevant["bar_close"] >= datetime.combine(
        local.date(), time(1, 5), tzinfo=ROME)]
    if stable.empty or stable["bar_close"].min() > datetime.combine(
            local.date(), time(2, 0), tzinfo=ROME):
        return False
    diffs = stable["bar_close"].diff().dropna().dt.total_seconds() / 60
    return bool(diffs.empty or diffs.max() <= 5.1)


def load_es_candles(day, required_until=None):
    """Massive se copre l'intero Replay; fallback Yahoo con controllo qualità.

    La scelta della fonte si applica al grafico INTERO, mai a singole barre.
    """
    _day_window(day)
    if required_until is not None and required_until.tzinfo is None:
        raise ValueError("Snapshot Replay privo di fuso orario")
    try:
        key = str(st.secrets.get("MASSIVE_API_KEY", "")).strip()
        contract = str(st.secrets.get("MASSIVE_ES_CONTRACT", "")).strip().upper()
    except Exception:
        key, contract = "", ""
    reason = ""
    if key and contract and required_until is not None:
        try:
            massive = _massive_candles(day, contract, key)
            if massive_covers_requested_interval(massive, required_until):
                return massive
            latest = massive["bar_close"].max().strftime("%H:%M")
            reason = f"Massive {contract} non copre ancora lo snapshot (ultima candela {latest} Roma)"
        except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
            # Non mostrare token o URL autenticati nell'interfaccia.
            reason = f"Massive {contract} non disponibile per il Replay ({type(exc).__name__})"
    else:
        reason = "Massive Futures non configurato per il Replay"
    yahoo = _yahoo_candles(day)
    yahoo.attrs["massive_fallback_reason"] = reason
    return yahoo


def candles_up_to(data, downloaded_at):
    """Nessuna candela aperta: solo close <= timestamp acquisizione reale."""
    if downloaded_at is None:
        return data.iloc[0:0]
    return data.loc[data["bar_close"] <= downloaded_at]

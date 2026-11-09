"""Replay ES: soltanto barre Massive Futures 5m verificate.

Se Massive non copre il periodo selezionato, il grafico con candele
non viene disegnato. Nessuna interpolazione o modifica ai dati NET GAMMA.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

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
    """Sola fonte Massive ES 5m: rifiuta dati mancanti o non aggiornati."""
    _day_window(day)
    if required_until is None or required_until.tzinfo is None:
        raise ValueError("Massive: orario snapshot assente o privo di fuso.")
    try:
        api_key = str(st.secrets.get("MASSIVE_API_KEY", "")).strip()
        contract = str(st.secrets.get("MASSIVE_ES_CONTRACT", "")).strip().upper()
    except Exception:
        api_key, contract = "", ""
    if not api_key or not contract:
        raise ValueError("Massive Futures ES non configurato nel Replay.")
    try:
        massive = _massive_candles(day, contract, api_key)
    except requests.RequestException as exc:
        raise ValueError("Massive Futures ES non raggiungibile o non autorizzato.") from exc
    if not massive_covers_requested_interval(massive, required_until):
        latest = massive["bar_close"].max().strftime("%H:%M")
        requested = required_until.astimezone(ROME).strftime("%H:%M")
        raise ValueError(
            f"Massive {contract}: ultima candela {latest} Roma; "
            f"rilevamento {requested}. Grafico candele non disponibile."
        )
    return massive


def candles_up_to(data, downloaded_at):
    """Nessuna candela aperta: solo close <= timestamp acquisizione reale."""
    if downloaded_at is None:
        return data.iloc[0:0]
    return data.loc[data["bar_close"] <= downloaded_at]

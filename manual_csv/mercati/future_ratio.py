"""Synchronized, read-only Yahoo 5-minute ETF/future reference prices.

One pair is frozen per original Cboe snapshot and chosen dated futures contract.
A shared timestamp is mandatory. These are *price-ratio projections*, NOT
native options-on-futures strikes or CME Greeks.
"""
from datetime import date, datetime, timedelta, timezone, time
from math import isfinite
from zoneinfo import ZoneInfo
from manual_csv.mercati.v2_term_structure import select_first_two, CurveUnavailable
import re
import requests
from copy import deepcopy
from functools import lru_cache
from threading import Lock
from time import monotonic, time as epoch_time

import pandas as pd
from manual_csv.mercati.v2_massive_day_cache import fetch_json as fetch_massive_json

NY = ZoneInfo("America/New_York")
INTRADAY_MINUTES = 5
MAX_QUOTE_AGE_MINUTES = 60
AUTO_OIL_CONTRACT = "Automatico · contratto CL più scambiato"
CONTRACTS = {
    "GLD": {
        "etf_yahoo": "GLD",
        "etf_tv": "AMEX:GLD",
        "contracts": {
            "GCZ26 · dicembre 2026": ("GCZ26.CMX", "COMEX:GCZ2026"),
            "GCG27 · febbraio 2027": ("GCG27.CMX", "COMEX:GCG2027"),
        },
    },
    "USO": {
        "etf_yahoo": "USO",
        "etf_tv": "AMEX:USO",
        "contracts": {
            "CLZ26 · dicembre 2026": ("CLZ26.NYM", "NYMEX:CLZ2026"),
            "CLF27 · gennaio 2027": ("CLF27.NYM", "NYMEX:CLF2027"),
        },
    },
}


def choose_contract(symbol: str, selection: str):
    if symbol not in CONTRACTS or selection not in CONTRACTS[symbol]["contracts"]:
        raise ValueError("Contratto future non autorizzato: seleziona un mese quotato.")
    return CONTRACTS[symbol]["contracts"][selection]


def normalize_bars(frame):
    """Canonical UTC close prices keyed by the opening time of 5m bars."""
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("Nessuna candela intraday fornita dal mercato.")
    if "Close" not in frame.columns:
        raise ValueError("Close mancante nei dati di mercato.")
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        raise ValueError("Timestamp senza fuso: impossibile sincronizzare.")
    series = pd.Series(pd.to_numeric(frame["Close"], errors="coerce").to_numpy(),
                       index=idx.tz_convert("UTC"))
    series = series[~series.index.duplicated(keep="last")]
    return series.sort_index()


def synchronized_pair(etf_bars, future_bars, etf_symbol, future_yahoo_ticker,
                      future_tv_symbol, target: date, now=None):
    """Last common *completed* 5m bar within ETF regular NY session of target.

    Rejects mismatched timestamps, stale intraday pairs, null/zero price,
    and stale/future bars. No nearest-neighbor matching (no false sync).
    """
    if not isinstance(target, date):
        raise ValueError("Data fotografia Cboe non valida.")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Orologio senza timezone.")
    now = now.astimezone(timezone.utc)
    etf = normalize_bars(etf_bars)
    fut = normalize_bars(future_bars)
    both = etf.index.intersection(fut.index)
    valid = []
    for stamp in both:
        dt = stamp.to_pydatetime().astimezone(timezone.utc)
        ny = dt.astimezone(NY)
        if ny.date() != target or not time(9, 30) <= ny.time() < time(16, 0):
            continue
        if dt + timedelta(minutes=INTRADAY_MINUTES) > now:
            continue
        e, f = float(etf.loc[stamp]), float(fut.loc[stamp])
        if not (isfinite(e) and e > 0 and isfinite(f) and f > 0):
            continue
        valid.append((dt, e, f))
    if not valid:
        raise ValueError("Nessuna coppia di barre GLD/GC o USO/CL con lo stesso "
                         "timestamp, completata e nella seduta ETF della data selezionata.")
    dt, ep, fp = max(valid, key=lambda v: v[0])
    # Reject delayed, stale or prior-session bars DURING today's ETF cash session.
    now_ny = now.astimezone(NY)
    if target == now_ny.date() and time(9, 30) <= now_ny.time() < time(16, 0):
        age = (now - (dt + timedelta(minutes=INTRADAY_MINUTES))).total_seconds() / 60
        if age > MAX_QUOTE_AGE_MINUTES:
            raise ValueError(f"Barre ETF/future troppo vecchie ({age:.0f} min): "
                             "fotografia sincronizzata annullata.")
    return {"etf_symbol": etf_symbol, "future_yahoo_ticker": future_yahoo_ticker,
            "future_tv_symbol": future_tv_symbol, "etf_price": ep, "future_price": fp,
            "ratio": fp / ep, "bar_utc": dt.isoformat(timespec="seconds"),
            "bar_ny": dt.astimezone(NY).isoformat(timespec="seconds"),
            "target_date": target.isoformat(), "interval": "5m",
            "source": "Yahoo Finance · 5m same-timestamp completed bars",
            "mode": "automatic-synchronized"}


def fetch_pair(symbol, selection, target: date, now=None, yf_module=None):
    """Fetch two selected Yahoo series; never silently switch future months."""
    ticker, tv = choose_contract(symbol, selection)
    if yf_module is None:
        import yfinance as yf
        yf_module = yf
    etf_ticker = CONTRACTS[symbol]["etf_yahoo"]
    etf = yf_module.Ticker(etf_ticker).history(
        period="5d", interval="5m", prepost=False, auto_adjust=False)
    future = yf_module.Ticker(ticker).history(
        period="5d", interval="5m", prepost=False, auto_adjust=False)
    return synchronized_pair(etf, future, symbol, ticker, tv, target, now=now)


def _dated_massive_gc_contract(yahoo_ticker: str) -> str:
    """Translate fixed Yahoo contract labels to the same dated Massive GC."""
    match = re.fullmatch(r"GC([FGHJKMNQUVXZ])(\d\d)[.]CMX", yahoo_ticker)
    if not match:
        raise ValueError("Solo contratti GLD/GC con scadenza esplicita.")
    return f"GC{match.group(1)}{int(match.group(2)) % 10}"


def _gold_massive_bars(ticker: str, target: date, api_key: str, get=None):
    """Closed GC 5m bars from a named Massive contract on the ETF session day."""
    if not str(api_key or "").strip():
        raise ValueError("MASSIVE_API_KEY mancante in Streamlit: GC non verificabile.")
    if not re.fullmatch(r"GC[FGHJKMNQUVXZ][0-9]", ticker):
        raise ValueError("Simbolo Massive GC non valido.")
    try:
        payload = fetch_massive_json(
            "https://api.massive.com/futures/v1/aggs/" + ticker,
            {"resolution": "5min",
             "window_start.gte": target.isoformat(),
             "window_start.lt": (target + timedelta(days=1)).isoformat(),
             "sort": "window_start.asc", "limit": 1000},
            api_key, get=get)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise ValueError(f"Massive GC: errore HTTP {status or 'non identificato'}") from exc
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise ValueError("Massive GC: dati intraday non accessibili.") from exc
    if (not isinstance(payload, dict)
            or payload.get("status") not in ("OK", "DELAYED")
            or not isinstance(payload.get("results"), list)
            or payload.get("next_url")):
        raise ValueError("Massive GC: barre 5 minuti assenti o incomplete.")
    records = []
    for r in payload["results"]:
        if (not isinstance(r, dict) or r.get("ticker") != ticker
                or r.get("session_end_date") != target.isoformat()):
            continue
        try:
            ts = pd.to_datetime(int(r["window_start"]), unit="ns", utc=True)
            c = float(r["close"])
            if isfinite(c) and c > 0:
                records.append((ts, c))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    if not records:
        raise ValueError(f"Massive GC: nessuna barra valida per {ticker} il {target}.")
    return pd.DataFrame(
        {"Close": [v for _, v in records]},
        index=pd.DatetimeIndex([t for t, _ in records])
    )


def fetch_gold_pair(selection, captured_at, api_key, yf_module=None, get=None):
    """Frozen Gold ETF/future mapping using last common bar BEFORE the capture.

    The acquisition timestamp is timezone-aware and more reliable than the
    raw Cboe timestamp with unspecified timezone. On 07:31 Rome Nov 2,
    last synchronized ETF cash data can legitimately be from Fri Oct 30.
    The selected 5m bar is never allowed to occur after the Cboe capture.
    """
    future_yahoo, future_tv = choose_contract("GLD", selection)
    future_massive = _dated_massive_gc_contract(future_yahoo)
    if isinstance(captured_at, str):
        try:
            captured_at = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValueError("Ora di acquisizione GLD non interpretabile.") from exc
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("Serve l'ora di acquisizione GLD con fuso orario.")
    cutoff = captured_at.astimezone(timezone.utc)
    if cutoff > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Acquisizione GLD nel futuro: controllo non possibile.")
    ny_day = cutoff.astimezone(NY).date()
    if (datetime.now(timezone.utc) - cutoff).days > 58:
        raise ValueError("Fotografia troppo vecchia per le barre ETF Yahoo 5m.")
    if yf_module is None:
        import yfinance as yf
        yf_module = yf
    try:
        etf = yf_module.Ticker("GLD").history(
            start=(ny_day-timedelta(days=10)).isoformat(),
            end=(ny_day+timedelta(days=1)).isoformat(),
            interval="5m", prepost=False, auto_adjust=False)
        etf_prices = normalize_bars(etf)
    except Exception as exc:
        raise ValueError("GLD Yahoo: storico 5 minuti non disponibile.") from exc
    # Find the most recent ETF cash session with completed bars BEFORE capture.
    latest_dates = set()
    for stamp in etf_prices.index:
        dt = stamp.to_pydatetime().astimezone(timezone.utc)
        ny = dt.astimezone(NY)
        if (ny.date() <= ny_day and time(9, 30) <= ny.time() < time(16)
                and dt + timedelta(minutes=5) <= cutoff):
            latest_dates.add(ny.date())
    if not latest_dates:
        raise ValueError("Nessuna seduta GLD completata prima della fotografia.")
    reference_day = max(latest_dates)
    if (ny_day-reference_day).days > 7:
        raise ValueError("Ultima seduta GLD troppo lontana dalla fotografia.")
    future_bars = _gold_massive_bars(future_massive, reference_day, api_key, get=get)
    pair = synchronized_pair(
        etf, future_bars, "GLD", future_yahoo, future_tv,
        reference_day, now=cutoff)
    pair["future_massive_ticker"] = future_massive
    pair["capture_cutoff_utc"] = cutoff.isoformat(timespec="seconds")
    pair["source"] = (
        f"Yahoo GLD + Massive {future_massive} · stessa barra 5m "
        f"chiusa prima dell'acquisizione Cboe")
    return pair


def _dated_massive_cl_contract(yahoo_ticker: str) -> str:
    """Translate fixed Yahoo contract labels to the same dated Massive CL."""
    match = re.fullmatch(r"CL([FGHJKMNQUVXZ])(\d\d)[.]NYM", yahoo_ticker)
    if not match:
        raise ValueError("Solo contratti USO/CL con scadenza esplicita.")
    return f"CL{match.group(1)}{int(match.group(2)) % 10}"


# Shared, in-process cache: saves completed Massive CL requests across Streamlit
# reruns, different Cboe photographs and the rollover + chart/Pine paths.
# A 429 at the final 5m step must NOT discard the three successful earlier calls.
# Never cache failures, incomplete pages, or results from a test-injected getter.
_CL_CACHE_LOCK = Lock()
_CL_COOLDOWN_UNTIL = 0.0
_CL_429_FALLBACK_SECONDS = 65


def _cl_cache_window(params):
    """Historical sessions: day cache. Current session: short-lived cache."""
    stamp = params.get("date") or params.get("window_start.lte")
    if not stamp and params.get("window_start.lt"):
        try:
            stamp = (date.fromisoformat(params["window_start.lt"]) -
                     timedelta(days=1)).isoformat()
        except (ValueError, TypeError):
            stamp = None
    try:
        session_day = date.fromisoformat(stamp) if stamp else None
    except (ValueError, TypeError):
        session_day = None
    # Invalidate the in-memory lru at ROME midnight, not UTC midnight.
    # Current-session observations refresh at a bounded 60-second cadence.
    from zoneinfo import ZoneInfo
    rome_day = int(datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y%m%d"))
    if session_day and session_day < datetime.now(NY).date():
        return rome_day
    return rome_day * 100000 + int(epoch_time() // 60) % 100000


def _cl_get_json_uncached(url, params, api_key, getter, *, enforce_cooldown=False):
    global _CL_COOLDOWN_UNTIL
    if enforce_cooldown:
        with _CL_CACHE_LOCK:
            remaining = _CL_COOLDOWN_UNTIL - monotonic()
        if remaining > 0:
            raise ValueError(
                f"Massive CL: limite API HTTP 429; attendi almeno "
                f"{int(remaining) + 1} secondi prima di riprovare.")
    try:
        if enforce_cooldown:
            payload = fetch_massive_json(url, params, api_key)
        else:
            response = getter(
                url, params=params,
                headers={"Authorization": "Bearer " + api_key,
                         "Accept": "application/json"},
                timeout=(6, 20))
            response.raise_for_status()
            payload = response.json()
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            if enforce_cooldown:
                # This prevents repeated retries from worsening the rate limit.
                # A valid bounded Retry-After header takes precedence.
                delay = _CL_429_FALLBACK_SECONDS
                try:
                    value = int(exc.response.headers.get("Retry-After", ""))
                    if 1 <= value <= 300:
                        delay = value
                except (AttributeError, TypeError, ValueError):
                    pass
                with _CL_CACHE_LOCK:
                    _CL_COOLDOWN_UNTIL = max(
                        _CL_COOLDOWN_UNTIL, monotonic() + delay)
            raise ValueError(
                "Massive CL: limite API HTTP 429. "
                "Le chiamate già riuscite sono conservate; "
                "riprovare dopo il periodo di attesa.") from exc
        raise ValueError(
            f"Massive CL: errore HTTP {status or 'non identificato'}") from exc
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise ValueError("Massive CL: dati non accessibili.") from exc
    if (not isinstance(payload, dict)
            or payload.get("status") not in ("OK", "DELAYED")
            or not isinstance(payload.get("results"), list)
            or payload.get("next_url")):
        raise ValueError("Massive CL: risposta JSON o paginazione incompleta.")
    return payload["results"]


@lru_cache(maxsize=384)
def _cl_cached_json(url, params_tuple, api_key, bucket):
    # Store only successfully validated results; failures do not enter cache.
    return _cl_get_json_uncached(
        url, dict(params_tuple), api_key, requests.get,
        enforce_cooldown=True)


def _cl_api_rows(url, params, api_key, get=None):
    """Reuse a successful CL response without rereading the Massive endpoint."""
    if get is not None:
        # Deterministic fake API reads in unit tests, without cache pollution.
        return _cl_get_json_uncached(url, params, api_key, get)
    key = tuple(sorted(params.items()))
    data = _cl_cached_json(url, key, api_key, _cl_cache_window(params))
    return deepcopy(data)


def _oil_massive_bars(ticker: str, target: date, api_key: str, get=None):
    """Closed CL 5m bars from a named Massive contract on the ETF session day."""
    if not str(api_key or "").strip():
        raise ValueError("MASSIVE_API_KEY mancante in Streamlit: CL non verificabile.")
    if not re.fullmatch(r"CL[FGHJKMNQUVXZ][0-9]", ticker):
        raise ValueError("Simbolo Massive CL non valido.")
    rows = _cl_api_rows(
        "https://api.massive.com/futures/v1/aggs/" + ticker,
        {"resolution": "5min",
         "window_start.gte": target.isoformat(),
         "window_start.lt": (target + timedelta(days=1)).isoformat(),
         "sort": "window_start.asc", "limit": 1000},
        api_key, get=get)
    records = []
    for r in rows:
        if (not isinstance(r, dict) or r.get("ticker") != ticker
                or r.get("session_end_date") != target.isoformat()):
            continue
        try:
            ts = pd.to_datetime(int(r["window_start"]), unit="ns", utc=True)
            c = float(r["close"])
            if isfinite(c) and c > 0:
                records.append((ts, c))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    if not records:
        raise ValueError(f"Massive CL: nessuna barra valida per {ticker} il {target}.")
    return pd.DataFrame(
        {"Close": [v for _, v in records]},
        index=pd.DatetimeIndex([t for t, _ in records])
    )


def _auto_oil_contract(reference_day: date, api_key: str, get=None):
    """Pick chart contract from the first two CL maturities by verified volume.

    PIT contracts + same-session 1session volumes strictly before capture.
    Not a reconstruction of USO's multi-day holdings rollover.
    """
    if not str(api_key or "").strip():
        raise ValueError("MASSIVE_API_KEY mancante per il rollover automatico CL.")
    def read_json(url, params):
        return _cl_api_rows(url, params, api_key, get=get)
    contracts = read_json(
        "https://api.massive.com/futures/v1/contracts",
        {"product_code": "CL", "date": reference_day.isoformat(),
         "active": "true", "type": "single", "sort": "ticker.asc", "limit": 1000}
    )
    try:
        front, second = select_first_two(contracts, "CL", reference_day)
    except CurveUnavailable as exc:
        raise ValueError("Rollover CL: due scadenze attive non verificabili.") from exc
    volumes = []
    for future in (front, second):
        rows = read_json(
            "https://api.massive.com/futures/v1/aggs/" + future["ticker"],
            {"resolution": "1session",
             "window_start.gte": (reference_day-timedelta(days=5)).isoformat(),
             "window_start.lte": reference_day.isoformat(),
             "sort": "window_start.desc", "limit": 9}
        )
        today = [row for row in rows if isinstance(row, dict)
                 and row.get("ticker") == future["ticker"]
                 and row.get("session_end_date") == reference_day.isoformat()]
        if len(today) != 1:
            raise ValueError("Rollover CL: volumi della stessa seduta non disponibili.")
        try:
            vol = int(today[0]["volume"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Rollover CL: volume non verificabile.") from exc
        if vol <= 0:
            raise ValueError("Rollover CL: volume nullo/non valido.")
        volumes.append(vol)
    selected = second if volumes[1] > volumes[0] else front
    match = re.fullmatch(r"CL([FGHJKMNQUVXZ])(\d)", selected["ticker"])
    if not match:
        raise ValueError("Rollover CL: formato contratto inatteso.")
    month, digit = match.groups()
    years = [y for y in range(reference_day.year, reference_day.year + 4)
             if y % 10 == int(digit)]
    if len(years) != 1:
        raise ValueError("Rollover CL: anno contratto non verificabile.")
    year = years[0]
    return {
        "ticker": selected["ticker"],
        "future_yahoo_ticker": f"CL{month}{year%100:02d}.NYM",
        "future_tv_symbol": f"NYMEX:CL{month}{year}",
        "reference_date": reference_day.isoformat(),
        "front_ticker": front["ticker"], "second_ticker": second["ticker"],
        "front_volume": volumes[0], "second_volume": volumes[1],
    }


def fetch_oil_pair(selection, captured_at, api_key, yf_module=None, get=None):
    """Frozen Oil ETF/future mapping using last common bar BEFORE the capture.

    The acquisition timestamp is timezone-aware and more reliable than the
    raw Cboe timestamp with unspecified timezone. On 07:31 Rome Nov 2,
    last synchronized ETF cash data can legitimately be from Fri Oct 30.
    The selected 5m bar is never allowed to occur after the Cboe capture.
    """
    automatic = selection == AUTO_OIL_CONTRACT
    if not automatic:
        future_yahoo, future_tv = choose_contract("USO", selection)
        future_massive = _dated_massive_cl_contract(future_yahoo)
    if isinstance(captured_at, str):
        try:
            captured_at = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValueError("Ora di acquisizione USO non interpretabile.") from exc
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("Serve l'ora di acquisizione USO con fuso orario.")
    cutoff = captured_at.astimezone(timezone.utc)
    if cutoff > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Acquisizione USO nel futuro: controllo non possibile.")
    ny_day = cutoff.astimezone(NY).date()
    if (datetime.now(timezone.utc) - cutoff).days > 58:
        raise ValueError("Fotografia troppo vecchia per le barre ETF Yahoo 5m.")
    if yf_module is None:
        import yfinance as yf
        yf_module = yf
    try:
        etf = yf_module.Ticker("USO").history(
            start=(ny_day-timedelta(days=10)).isoformat(),
            end=(ny_day+timedelta(days=1)).isoformat(),
            interval="5m", prepost=False, auto_adjust=False)
        etf_prices = normalize_bars(etf)
    except Exception as exc:
        raise ValueError("USO Yahoo: storico 5 minuti non disponibile.") from exc
    # Find the most recent ETF cash session with completed bars BEFORE capture.
    latest_dates = set()
    for stamp in etf_prices.index:
        dt = stamp.to_pydatetime().astimezone(timezone.utc)
        ny = dt.astimezone(NY)
        if (ny.date() <= ny_day and time(9, 30) <= ny.time() < time(16)
                and dt + timedelta(minutes=5) <= cutoff):
            latest_dates.add(ny.date())
    if not latest_dates:
        raise ValueError("Nessuna seduta USO completata prima della fotografia.")
    reference_day = max(latest_dates)
    if (ny_day-reference_day).days > 7:
        raise ValueError("Ultima seduta USO troppo lontana dalla fotografia.")
    auto_contract = None
    if automatic:
        auto_contract = _auto_oil_contract(reference_day, api_key, get=get)
        future_yahoo = auto_contract["future_yahoo_ticker"]
        future_tv = auto_contract["future_tv_symbol"]
        future_massive = auto_contract["ticker"]
    future_bars = _oil_massive_bars(future_massive, reference_day, api_key, get=get)
    pair = synchronized_pair(
        etf, future_bars, "USO", future_yahoo, future_tv,
        reference_day, now=cutoff)
    pair["future_massive_ticker"] = future_massive
    pair["capture_cutoff_utc"] = cutoff.isoformat(timespec="seconds")
    pair["auto_contract"] = auto_contract
    pair["source"] = (
        f"Yahoo USO + Massive {future_massive} · stessa barra 5m "
        f"chiusa prima dell'acquisizione Cboe")
    return pair


def manual_pair(etf_price, future_price, symbol, selection, target, clock_ny):
    """Explicitly user-attested same-time reference pair, never labeled verified."""
    ticker, tv = choose_contract(symbol, selection)
    try:
        e, f = float(etf_price), float(future_price)
    except (ValueError, TypeError) as exc:
        raise ValueError("Prezzi manuali non numerici.") from exc
    if not all(isfinite(v) and v > 0 for v in (e, f)):
        raise ValueError("Prezzi manuali non validi.")
    try:
        t = datetime.fromisoformat(f"{target.isoformat()}T{clock_ny}")
        if not time(9,30) <= t.time() <= time(16,0):
            raise ValueError()
        stamp = t.replace(tzinfo=NY)
    except (ValueError, TypeError) as exc:
        raise ValueError("Indicare HH:MM New York nella sessione ETF 09:30–16:00.") from exc
    return {"etf_symbol": symbol, "future_yahoo_ticker": ticker,
            "future_tv_symbol": tv, "etf_price": e, "future_price": f,
            "ratio": f/e, "bar_utc": stamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "bar_ny": stamp.isoformat(timespec="seconds"),
            "target_date": target.isoformat(), "interval": "manual",
            "source": "Prezzi inseriti dall'utente; sincronizzazione dichiarata NON verificata",
            "mode": "manual-attested"}

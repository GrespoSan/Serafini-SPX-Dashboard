"""PIT first/second listed futures expiration and same-session settlement prices.

Massive Contracts point-in-time provides the two earliest actively traded
SINGLE contracts of each product NQ/GC/CL by last_trade_date, not alphabetic
ticker order. Their settlement prices must exist for the very same closed
session, from Massive 1session aggregates. Any missing/mismatched data => N/D.
Only read-only Massive requests, no Cboe, Supabase or scheduled download.
"""
import math
import re
from datetime import date, timedelta

import requests
from manual_csv.mercati.v2_massive_day_cache import fetch_json as fetch_massive_json

API = "https://api.massive.com/futures/v1"
PRODUCTS = frozenset(("NQ", "GC", "CL"))
TICKERS = {
    product: re.compile(rf"^{product}[FGHJKMNQUVXZ][0-9]{{1,2}}$")
    for product in PRODUCTS
}


class CurveUnavailable(ValueError):
    """Absent or non-comparable historical futures term structure."""


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CurveUnavailable("Settlement non numerico") from exc
    if not math.isfinite(number) or number <= 0:
        raise CurveUnavailable("Settlement non valido")
    return number


def select_first_two(contracts, product, session_day):
    """Two earliest distinct future expiries *active on that trading date*."""
    if product not in PRODUCTS or not isinstance(session_day, date):
        raise CurveUnavailable("Prodotto o data non validi")
    valid = []
    for item in contracts:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "")
        if (item.get("product_code") != product
                or not TICKERS[product].fullmatch(ticker)
                or item.get("active") is not True
                or item.get("type") not in ("single", None)):
            continue
        try:
            last = date.fromisoformat(item["last_trade_date"])
            first = date.fromisoformat(item["first_trade_date"])
        except (TypeError, ValueError, KeyError):
            continue
        if not first <= session_day <= last:
            continue
        valid.append({"ticker": ticker, "last_trade_date": last})
    valid.sort(key=lambda x: (x["last_trade_date"], x["ticker"]))
    selected, seen = [], set()
    for item in valid:
        # An ambiguous pair of instruments with one expiry is not "two expiries".
        if item["last_trade_date"] in seen:
            continue
        selected.append(item)
        seen.add(item["last_trade_date"])
        if len(selected) == 2:
            return selected
    raise CurveUnavailable("Mancano due scadenze attive distinte")


def settlement_for_session(payload, ticker, session_day):
    """Read the exchange settlement, not a mixed settlement/trade close."""
    if not isinstance(payload, dict) or payload.get("status") not in ("OK", "DELAYED"):
        raise CurveUnavailable("Risposta Massive aggregates non valida")
    if not isinstance(payload.get("results"), list):
        raise CurveUnavailable("Barre Massive non disponibili")
    matches = [
        item for item in payload["results"] if isinstance(item, dict)
        and item.get("ticker") == ticker
        and item.get("session_end_date") == session_day.isoformat()
    ]
    if len(matches) != 1:
        raise CurveUnavailable(f"Settlement sessione non univoco per {ticker}")
    return _positive(matches[0].get("settlement_price"))


def compare_settlements(front, second, front_price, second_price, session_day):
    """Regime measured by (F2/F1 - 1)*100 on a common settlement date."""
    front_price, second_price = _positive(front_price), _positive(second_price)
    if front["last_trade_date"] >= second["last_trade_date"]:
        raise CurveUnavailable("Ordine di scadenza non coerente")
    term_pct = 100 * (second_price / front_price - 1)
    return {
        "status": "VALIDO", "day": session_day.isoformat(),
        "front": front["ticker"], "second": second["ticker"],
        "front_expiry": front["last_trade_date"].isoformat(),
        "second_expiry": second["last_trade_date"].isoformat(),
        "front_settlement": front_price, "second_settlement": second_price,
        "term_pct": term_pct,
        "regime": "BACKWARDATION" if term_pct < 0 else "CONTANGO",
        "source": "Massive Futures REST · contracts PIT + 1session settlement_price",
    }


def _json_get(url, params, api_key, requester):
    try:
        payload = fetch_massive_json(
            url, params, api_key,
            get=None if requester is requests else requester.get)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            reason = "Massive: limite richieste API raggiunto (HTTP 429)"
        elif status in (401, 403):
            reason = "Massive: autorizzazione o piano Futures insufficiente"
        elif status == 400:
            reason = "Massive: parametri della richiesta non validi (HTTP 400)"
        else:
            reason = "Massive: risposta HTTP non valida"
        raise CurveUnavailable(reason) from exc
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        raise CurveUnavailable("API Massive non accessibile o risposta non valida") from exc
    if not isinstance(payload, dict):
        raise CurveUnavailable("JSON Massive non valido")
    return payload


def fetch_term_structure(product, session_day, api_key, requester=None):
    """On-demand only, exactly 1 contracts query + 2 settlement queries."""
    if product not in PRODUCTS or not isinstance(session_day, date):
        raise CurveUnavailable("Prodotto/data richiesti non validi")
    if not str(api_key or "").strip():
        raise CurveUnavailable("MASSIVE_API_KEY non configurata")
    requester = requester or requests

    contracts = _json_get(
        API + "/contracts",
        {"product_code": product, "date": session_day.isoformat(),
         "active": "true", "type": "single",
         # Massive Contracts accepts ticker/date/product_code for sort only.
         # Sorting by last_trade_date is done locally in select_first_two().
         "sort": "ticker.asc", "limit": 1000},
        api_key, requester
    )
    if contracts.get("status") not in ("OK", "DELAYED"):
        raise CurveUnavailable("Lista contratti Massive non valida")
    if contracts.get("next_url"):
        raise CurveUnavailable("Lista contratti incompleta: paginazione")
    if not isinstance(contracts.get("results"), list):
        raise CurveUnavailable("Nessun elenco contratti disponibile")
    front, second = select_first_two(contracts["results"], product, session_day)

    # Massive's window_start is the prior evening, NOT session_end_date.
    params = {"resolution": "1session",
              "window_start.gte": (session_day - timedelta(days=5)).isoformat(),
              "window_start.lte": session_day.isoformat(),
              "sort": "window_start.desc", "limit": 9}
    settles = []
    for item in (front, second):
        raw = _json_get(API + "/aggs/" + item["ticker"], params, api_key, requester)
        settles.append(settlement_for_session(raw, item["ticker"], session_day))
    return compare_settlements(front, second, settles[0], settles[1], session_day)

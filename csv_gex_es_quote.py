"""ES futures quote from Yahoo Chart, delayed/indicative availability only.

On the GEX chart this is a DIRECT futures price marker, NEVER a SPX
spot plus basis estimate. Primary: explicit front quarterly CME contract.
Fallback: ES=F continuous Yahoo future (always prominently labeled).
Network errors and stale quotes return N/D; no invented price/timestamp.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROME=ZoneInfo("Europe/Rome")
NY=ZoneInfo("America/New_York")
MONTH_CODES={3:"H",6:"M",9:"U",12:"Z"}


def _third_friday(year, month):
    d=date(year,month,1)
    return d+timedelta(days=(4-d.weekday())%7+14)


def front_symbol(on_date: date):
    """Conservative quarterly rollover 8 calendar days before expiry."""
    for year in range(on_date.year,on_date.year+3):
        for month,code in MONTH_CODES.items():
            third=_third_friday(year,month)
            if on_date < third-timedelta(days=8):
                return f"ES{code}{year%100:02d}.CME"
    raise ValueError("Nessun contratto trimestrale ES individuato")


def parse_chart(body: bytes, ticker: str, now: datetime):
    """Reject unavailable/stale/non-finite quotes and non-original timestamps."""
    doc=json.loads(body)
    results=(doc.get("chart") or {}).get("result") or []
    if not results:
        raise ValueError("Yahoo non restituisce una serie ES")
    item=results[0]
    meta=item.get("meta") or {}
    rows=((item.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    times=item.get("timestamp") or []
    pairs=[]
    for t,price in zip(times,rows):
        try:
            val=float(price)
            stamp=datetime.fromtimestamp(int(t),timezone.utc)
            age=(now-stamp).total_seconds()/60
            if math.isfinite(val) and val>0 and -2<=age<=90:
                pairs.append((stamp,val))
        except (TypeError,OverflowError,ValueError,OSError):
            continue
    if not pairs:
        raise ValueError("Prezzo ES assente oppure più vecchio di 90 minuti")
    stamp,price=max(pairs,key=lambda x:x[0])
    actual=meta.get("symbol")
    if actual and str(actual).upper()!=ticker.upper():
        raise ValueError("Simbolo Yahoo differente dal future richiesto")
    return {
        "price":round(price,4),
        "symbol":ticker,
        "source":"YAHOO_FINANCE_CHART",
        "observed_utc":stamp.isoformat(),
        "observed_rome":stamp.astimezone(ROME).strftime("%d/%m/%Y %H:%M:%S"),
        "age_minutes":round((now-stamp).total_seconds()/60,1),
        "delayed_label":"Yahoo Finance · quote potenzialmente ritardate",
        "contract_kind":"EXPLICIT" if ticker!="ES=F" else "CONTINUOUS",
    }


def fetch_es_quote(*, now=None, request=None):
    """Prefer ESZ26.CME etc; use ES=F only with explicit identity."""
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Ora senza fuso")
    on=now.astimezone(NY).date()
    primary=front_symbol(on)
    failures=[]
    for symbol in (primary,"ES=F"):
        url=(f"https://query1.finance.yahoo.com/v8/finance/chart/"
             f"{quote(symbol,safe='')}?range=1d&interval=1m")
        try:
            if request:
                body=request(url)
            else:
                r=Request(url,headers={"User-Agent":"Mozilla/5.0",
                                       "Accept":"application/json"})
                with urlopen(r,timeout=14) as response:
                    body=response.read(2_000_001)
            if not isinstance(body,bytes) or len(body)>2_000_000:
                raise ValueError("Dati Yahoo non validi o troppo grandi")
            result=parse_chart(body,symbol,now)
            result["preferred_contract"]=primary
            return result
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}")
    return {"price":None,"source":"YAHOO_FINANCE_CHART","symbol":primary,
            "observed_utc":None,"age_minutes":None,
            "error":"Nessuna quotazione ES utilizzabile ("+"; ".join(failures)+")"}

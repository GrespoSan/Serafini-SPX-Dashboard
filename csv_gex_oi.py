"""OCC public Series Search for Serafini CSV GEX ONLY.

No Alpaca credentials. The OCC report does not certify an OI settlement
date: retrieval time is NOT the OI date. Never mix OI sources silently.
Historical CSVs cannot use today's OCC values as if they were historical.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY=ZoneInfo("America/New_York")
OCC_ENDPOINT="https://marketdata.theocc.com/series-search"
MAX_BYTES=25_000_000
OCC_RE=re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
TICKER_RE=re.compile(r"^[A-Z]{1,6}$")


class OiSourceError(ValueError):
    pass


def parse_series(text: str, *, accepted: set[str]) -> dict[str,int]:
    """Read OCC Series Search, by exact contract OCC code (SPX and SPXW)."""
    if "Series Search Results" not in text[:1000]:
        raise OiSourceError("Risposta OCC senza intestazione Series Search Results")
    result={}
    for raw in text.splitlines():
        tokens=raw.split()
        if len(tokens)<9 or not (tokens[1].isdigit() and len(tokens[1])==4):
            continue
        root,yyyy,mm,dd,whole,decimals=tokens[:6]
        if not (root.isalpha() and root.isupper() and
                all(x.isdigit() for x in (mm,dd,whole,decimals))):
            continue
        try:
            date(int(yyyy),int(mm),int(dd))
            strike=int(whole)*1000+int(decimals.ljust(3,"0")[:3])
            if strike<=0: continue
            prefix=f"{root}{yyyy[2:]}{int(mm):02d}{int(dd):02d}"
        except (ValueError,OverflowError):
            continue
        remaining=tokens[6:]
        sides={x for x in remaining if x in ("C","P")}
        numbers=[x for x in remaining if x not in ("C","P")]
        if len(numbers)<2 or not numbers[0].isdigit() or not numbers[1].isdigit():
            continue
        for side,index in (("C",0),("P",1)):
            code=f"{prefix}{side}{strike:08d}"
            if side in sides and code in accepted:
                value=int(numbers[index])
                if code in result and result[code]!=value:
                    raise OiSourceError(f"OCC duplicato con Open Interest diverso: {code}")
                result[code]=value
    return result


def fetch_occ_oi(symbol: str, accepted: set[str], *, request=None,
                 clock=None) -> tuple[dict[str,int],dict]:
    """Fetch current public OCC series; unknown rows remain unknown, not zero."""
    symbol=str(symbol).upper().strip()
    if not TICKER_RE.fullmatch(symbol):
        raise OiSourceError("Sottostante OCC non valido")
    if not isinstance(accepted,set) or not accepted:
        raise OiSourceError("Nessun contratto CSV per il confronto OCC")
    if len(accepted)>100_000 or not all(OCC_RE.fullmatch(x) for x in accepted):
        raise OiSourceError("Identificatori opzioni CSV non validi")
    url=OCC_ENDPOINT+"?"+urlencode({"symbolType":"U","symbol":symbol})
    def download():
        req=Request(url,headers={
            "User-Agent":"Serafini-CSV-Research/1.0",
            "Accept":"text/plain",
        })
        with urlopen(req,timeout=35) as response:
            return response.read(MAX_BYTES+1)
    try:
        body=request(url) if request is not None else download()
    except Exception as exc:
        raise OiSourceError(f"OCC non disponibile ({type(exc).__name__})") from exc
    if not isinstance(body,bytes) or len(body)>MAX_BYTES:
        raise OiSourceError("File OCC non valido oppure oltre 25 MB")
    result=parse_series(body.decode("utf-8-sig",errors="replace"),accepted=accepted)
    if not result:
        raise OiSourceError("OCC: nessun identificatore CSV riconciliato")
    now=clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise OiSourceError("Ora download OCC senza fuso")
    return result,{
        "source":"OCC_SERIES_SEARCH",
        "underlying":symbol,
        "download_utc":now.astimezone(timezone.utc).isoformat(),
        "oi_asof_date":None,
        "oi_asof_status":"NON_PUBBLICATA_NEL_REPORT",
        "match_count":len(result),
        "csv_count":len(accepted),
    }


def apply_mode(contracts, *, mode: str, captured: datetime,
               target_expiries: dict, spot: float,
               occ: dict[str,int]|None, provenance: dict|None,
               fetch_error: str|None=None):
    """Switch entire GEX study to OCC or CSV. Auto fallback is explicit."""
    if mode not in ("csv","occ","auto"):
        raise ValueError("Fonte OI GEX sconosciuta")
    info={
        "requested_mode":mode,"effective_source":"CSV_CBOE",
        "oi_asof_date":None,"oi_date_status":"NON_DOCUMENTATA_NEL_CSV",
        "occ_download_utc":None,"matched":0,"eligible":0,
        "match_pct":None,"fallback_reason":None,"mixed_sources":False,
    }
    if mode=="csv":
        return contracts,info
    error=fetch_error
    now=None
    if error is None:
        if not provenance or provenance.get("source")!="OCC_SERIES_SEARCH":
            error="Identità della fonte OCC non verificabile"
        else:
            try:
                now=datetime.fromisoformat(str(provenance["download_utc"]).replace("Z","+00:00"))
                if now.tzinfo is None: raise ValueError("senza fuso")
                if captured.astimezone(NY).date()!=now.astimezone(NY).date():
                    raise ValueError("CSV storico incompatibile con OCC corrente")
                age=(datetime.now(timezone.utc)-now.astimezone(timezone.utc)).total_seconds()
                if age>3600 or age < -120:
                    raise ValueError("Orario download OCC fuori dalla finestra di un'ora")
            except (ValueError,TypeError,KeyError) as exc:
                error=f"OCC non contemporaneo al CSV: {str(exc)[:100]}"
    band=[c for c in contracts if c.expiry in set(target_expiries.values())
          and abs(c.strike/spot-1)<=.10]
    known=sum(c.symbol in (occ or {}) for c in band)
    ratio=known/len(band) if band else 0.0
    info.update(matched=known,eligible=len(band),match_pct=round(ratio*100,2))
    if error is None and (len(band)<20 or ratio<.95):
        error=f"Riconciliazione OCC insufficiente: {known}/{len(band)} nella banda ±10% (minimo 95%)"
    if error is None:
        # Do not let a strong 0DTE mask poor Weekly coverage or vice versa.
        for expiry in sorted(set(target_expiries.values())):
            group=[c for c in band if c.expiry==expiry]
            if not group:
                continue
            matched=sum(c.symbol in occ for c in group)
            if matched/len(group)<.95:
                error=(f"OCC {expiry.isoformat()}: solo {matched}/{len(group)} "
                       "contratti riconciliati (minimo 95%)")
                break
    if error:
        if mode=="occ":
            raise OiSourceError(error)
        info["fallback_reason"]=error
        return contracts,info
    import copy
    switched=copy.deepcopy(contracts)
    for c in switched:
        if c.symbol in occ:
            c.oi=int(occ[c.symbol])
            c.oi_source="occ_series_search"
            c.oi_date=None
        else:
            c.oi=None
            c.oi_source="occ_missing"
            c.oi_date=None
    info.update(
        effective_source="OCC_SERIES_SEARCH",
        oi_date_status="OCC_NON_DICHIARA_DATA_OI_SERIE",
        occ_download_utc=now.astimezone(timezone.utc).isoformat(),
        fallback_reason=None,
    )
    return switched,info

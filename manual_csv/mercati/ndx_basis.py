"""NQ–NDX previous cash-session basis on a dated CME Nasdaq future.

Uses a dated NQ quarterly contract chosen automatically by the official
CME equity-index roll convention (Monday before the expiration Friday).
The historical reference comes from the 15-minute aligned cash session,
never an unlabelled Yahoo NQ=F continuous contract.
No interpolation and no fabricated fallback on missing market data.
"""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from manual_csv.mercati.quarterly_contract import quarterly_contract
from manual_csv.mercati.v2_massive_day_cache import fetch_json as fetch_massive_json

NY=ZoneInfo('America/New_York')
MASSIVE_FUTURES_API="https://api.massive.com/futures/v1/aggs"


def nq_contract_for_date(trading_day: date) -> dict:
    return quarterly_contract("NQ", trading_day)


def _dated_nq_bars(contract: str, reference_day: date, api_key: str,
                   get=None) -> pd.Series:
    """15m Massive bars at real UTC start timestamps for a single dated NQ."""
    if not str(api_key or "").strip():
        raise ValueError("MASSIVE_API_KEY assente: differenziale NQ non verificabile")
    try:
        obj=fetch_massive_json(
            f"{MASSIVE_FUTURES_API}/{contract}",
            {"resolution":"15min",
             "window_start.gte":reference_day.isoformat(),
             "window_start.lt":(reference_day+timedelta(days=1)).isoformat(),
             "sort":"window_start.asc","limit":300},
            api_key,get=get)
    except (requests.RequestException,ValueError,TypeError) as exc:
        raise ValueError("Massive: intraday NQ non disponibile per il contratto scelto") from exc
    if (not isinstance(obj,dict) or obj.get("status") not in ("OK","DELAYED")
            or not isinstance(obj.get("results"),list) or obj.get("next_url")):
        raise ValueError("Massive: risposta intraday NQ incompleta o non verificabile")

    records=[]
    for row in obj["results"]:
        if (not isinstance(row,dict) or row.get("ticker")!=contract
                or row.get("session_end_date")!=reference_day.isoformat()):
            continue
        try:
            stamp=pd.to_datetime(int(row["window_start"]),unit="ns",utc=True)
            close=float(row["close"])
            if pd.notna(close):
                records.append((stamp,close))
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
    if not records:
        raise ValueError(f"Massive: nessuna barra intraday valida per {contract} alla data {reference_day}")
    result=pd.Series(
        [v for _,v in records],
        index=pd.DatetimeIndex([t for t,_ in records]))
    return result[~result.index.duplicated(keep="last")].sort_index()


def aligned_basis(nq: pd.Series, ndx: pd.Series, day:date):
    if nq.empty or ndx.empty or nq.index.tz is None or ndx.index.tz is None:
        raise ValueError('Dati NQ/NDX vuoti o senza fuso orario')
    x=nq.copy();y=ndx.copy()
    x.index=x.index.tz_convert(NY);y.index=y.index.tz_convert(NY)
    values=pd.concat([pd.to_numeric(x,errors='coerce').rename('NQ'),
                      pd.to_numeric(y,errors='coerce').rename('NDX')],axis=1).dropna().sort_index()
    values=values[~values.index.duplicated(keep='last')]
    values=values[(values.index.time>=time(9,30)) & (values.index.time<time(16))]
    values=values[values.index.date<day]
    dates=sorted(set(values.index.date))
    if not dates:
        raise ValueError('Nessuna seduta precedente comune')
    selected=dates[-1]
    bars=values[values.index.date==selected]
    if len(bars)<20 or bars.index[0].time()>time(9,45) or bars.index[-1].time()<time(15,30):
        raise ValueError(f'Seduta {selected}: dati insufficienti ({len(bars)} barre comuni)')
    delta=bars['NQ']-bars['NDX']
    return {'reference_date':selected,'basis':float(delta.mean()),
            'minimum':float(delta.min()),'maximum':float(delta.max()),
            'last':float(delta.iloc[-1]),'bar_count':len(delta),
            'source':'Yahoo Finance NQ=F / ^NDX · 15m allineati seduta cash precedente'}


def _close(frame, ticker):
    if frame is None or frame.empty:
        raise ValueError(f'Serie Yahoo assente: {ticker}')
    if isinstance(frame.columns,pd.MultiIndex):
        for k in [('Close',ticker),(ticker,'Close')]:
            if k in frame.columns:return frame[k].dropna()
        found=[c for c in frame.columns if 'Close' in c]
        if not found:raise ValueError('Chiusura non trovata')
        return frame[found[0]].dropna()
    return frame['Close'].dropna()


def fetch_basis(day:date, massive_api_key: str=""):
    """Prior cash-session 15m average: dated CME NQ on Massive minus Yahoo NDX.

    The same mean/min/max/count formula of aligned_basis is unchanged.
    Only the previously ambiguous NQ=F price source is replaced.
    """
    import yfinance as yf
    today=datetime.now(NY).date()
    if day > today + timedelta(days=1) or day < today - timedelta(days=50):
        raise ValueError("Data fuori dalla finestra Yahoo a 15 minuti (circa 50 giorni)")

    # The leading contract is determined by the *analysis* session day.
    contract=nq_contract_for_date(day)
    start=str(day-timedelta(days=10))
    end=str(min(today+timedelta(days=1),day+timedelta(days=1)))
    ndx_data=yf.download("^NDX",start=start,end=end,interval="15m",
                         auto_adjust=False,prepost=False,progress=False,threads=False)
    ndx=_close(ndx_data,"^NDX")
    if ndx.empty or ndx.index.tz is None:
        raise ValueError("Indice NDX: chiusure intraday non disponibili")
    ndx_ny=ndx.copy()
    ndx_ny.index=ndx_ny.index.tz_convert(NY)
    eligible=ndx_ny[(ndx_ny.index.time>=time(9,30)) &
                    (ndx_ny.index.time<time(16,0)) &
                    (ndx_ny.index.date<day)]
    dates=sorted(set(eligible.index.date))
    if not dates:
        raise ValueError("Indice NDX: nessuna seduta cash precedente")
    prior_day=dates[-1]
    future=_dated_nq_bars(contract["ticker"],prior_day,massive_api_key)
    result=aligned_basis(future,ndx,day)
    if result["reference_date"]!=prior_day:
        raise ValueError("Sedute NQ e NDX non coincidenti")
    result.update({
        "ticker":contract["ticker"],
        "tv_contract":contract["tv_symbol"],
        "roll_day":contract["roll_day"],
        "source":f"Massive Futures {contract['ticker']} + Yahoo Finance ^NDX · 15m sincronizzati",
    })
    return result

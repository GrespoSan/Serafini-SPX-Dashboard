"""Range asiatico ES, 00:00–09:00 Europe/Rome, con controllo della copertura dati.

Fonte gratuita Yahoo Finance ES=F a 15 minuti. Non equivale al feed CQG/StoneX.
Nessuna interpolazione di barre assenti, nessuna stima del range se incompleto.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
import pandas as pd

ROME = ZoneInfo('Europe/Rome')


def _extract_ohlc(frame, symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise ValueError('Yahoo non ha restituito barre ES')
    data = {}
    for c in ('High', 'Low'):
        if isinstance(frame.columns, pd.MultiIndex):
            if (c, symbol) in frame.columns:
                data[c] = frame[(c, symbol)]
            elif (symbol, c) in frame.columns:
                data[c] = frame[(symbol, c)]
            else:
                col = frame.xs(c, axis=1, level=0) if c in frame.columns.get_level_values(0) else frame.xs(c, axis=1, level=1)
                data[c] = col.iloc[:, 0]
        else:
            data[c] = frame[c]
    df = pd.DataFrame(data)
    df.index = frame.index
    if df.index.tz is None:
        raise ValueError('Timestamp senza fuso: impossibile individuare 00:00–09:00 Roma')
    df.index = df.index.tz_convert(ROME)
    return df.apply(pd.to_numeric, errors='coerce').dropna().sort_index()


def calculate_asia_range(bars: pd.DataFrame, target: date) -> dict:
    """Richiede tutte le 36 barre 15m con apertura alle 00:00 .. 08:45 Roma.

    Le barre hanno timestamp di APERTURA, quindi la barra 08:45 chiude alle 09:00.
    """
    df = bars.copy()
    if df.index.tz is None:
        raise ValueError('Timestamp delle barre senza fuso orario')
    df.index = df.index.tz_convert(ROME)
    start = datetime.combine(target, time(0, 0), tzinfo=ROME)
    expected = pd.date_range(start=start, periods=36, freq='15min')
    actual = df[~df.index.duplicated(keep='last')].reindex(expected)
    if actual[['High','Low']].isna().any().any():
        valid = int(actual[['High','Low']].notna().all(axis=1).sum())
        raise ValueError(f'Range non disponibile: {valid}/36 barre ES 15m fra 00:00 e 09:00 Roma')
    if (actual['High'] < actual['Low']).any():
        raise ValueError('Dati ES non validi: massimo inferiore al minimo')
    return {'date': target.isoformat(), 'high': float(actual['High'].max()),
            'low': float(actual['Low'].min()), 'bars': 36,
            'source': 'Yahoo Finance ES=F (15m, 00:00–09:00 Europe/Rome)'}


def download_asia_range(target: date) -> dict:
    import yfinance as yf
    now = datetime.now(ROME)
    if target > now.date():
        raise ValueError('La data richiesta è futura')
    if target == now.date() and now.time() < time(9, 0):
        raise ValueError('Il box 00:00–09:00 Roma non è ancora completato')
    if target < now.date() - timedelta(days=50):
        raise ValueError('Yahoo non offre storico 15m affidabile oltre circa 50 giorni')
    # Una finestra di 3 giorni evita problemi quando il range attraversa
    # la data di contrattazione CME o l’ora legale.
    raw = yf.download('ES=F', start=str(target-timedelta(days=1)),
                      end=str(target+timedelta(days=2)), interval='15m',
                      auto_adjust=False, prepost=True, progress=False, threads=False)
    return calculate_asia_range(_extract_ohlc(raw, 'ES=F'), target)

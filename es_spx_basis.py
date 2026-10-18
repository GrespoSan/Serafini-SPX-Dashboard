"""Stima ES-SPX da chiusure a 15m sincronizzate della sessione cash precedente.

Fonte sperimentale Yahoo Finance: ES=F (continuous front month) e ^GSPC.
Non garantisce identità esatta col future CME usato su TradingView.
Nessun valore viene interpolato o stimato in assenza di dati validi.
"""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo('America/New_York')


def previous_cash_session(target: date, sessions: list[date]) -> date | None:
    """Ultima sessione disponibile strettamente precedente alla data target."""
    eligible = [day for day in sessions if day < target]
    return max(eligible) if eligible else None


def calculate_basis(es: pd.Series, spx: pd.Series, target: date) -> dict:
    """Allinea per istante le chiusure delle barre a 15m, 09:30-16:00 ET.

    Non presume che la seduta precedente sia un giorno lavorativo standard:
    viene selezionata dalle sedute realmente presenti in entrambe le serie.
    """
    if es.empty or spx.empty:
        raise ValueError('Serie ES o SPX vuota')
    if es.index.tz is None or spx.index.tz is None:
        raise ValueError('I timestamp intraday devono contenere il fuso orario')
    a = es.copy(); b = spx.copy()
    a.index = a.index.tz_convert(ET)
    b.index = b.index.tz_convert(ET)
    a = pd.to_numeric(a, errors='coerce').rename('ES')
    b = pd.to_numeric(b, errors='coerce').rename('SPX')
    aligned = pd.concat([a, b], axis=1, join='inner').dropna().sort_index()
    aligned = aligned[~aligned.index.duplicated(keep='last')]
    aligned = aligned[(aligned.index.time >= time(9, 30)) & (aligned.index.time < time(16, 0))]
    aligned = aligned[aligned.index.date < target]
    days = sorted(set(aligned.index.date))
    ref_day = previous_cash_session(target, days)
    if ref_day is None:
        raise ValueError('Nessuna seduta precedente con dati ES/SPX allineati')
    bars = aligned[aligned.index.date == ref_day].copy()
    # Sessione ordinaria completa: 26 barre da 15 minuti; i provider possono
    # omettere la prima barra, o l'ultima, quindi esigiamo almeno 20.
    if len(bars) < 20:
        raise ValueError(f'Seduta {ref_day}: solo {len(bars)} barre allineate, insufficienti (minimo 20)')
    if bars.index[0].time() > time(9, 45) or bars.index[-1].time() < time(15, 30):
        raise ValueError('Dati cash incompleti all’inizio o alla fine della seduta')
    bars['ES-SPX'] = bars['ES'] - bars['SPX']
    return {
        'reference_date': ref_day,
        'basis': float(bars['ES-SPX'].mean()),
        'bar_count': len(bars),
        'minimum': float(bars['ES-SPX'].min()),
        'maximum': float(bars['ES-SPX'].max()),
        'last': float(bars['ES-SPX'].iloc[-1]),
        'bars': bars,
    }


def _close_series(frame, symbol):
    """Gestisce colonne Yahoo normali e MultiIndex."""
    if frame is None or frame.empty:
        raise ValueError(f'Dati Yahoo mancanti per {symbol}')
    if isinstance(frame.columns, pd.MultiIndex):
        if ('Close', symbol) in frame.columns:
            series = frame[('Close', symbol)]
        elif (symbol, 'Close') in frame.columns:
            series = frame[(symbol, 'Close')]
        else:
            vals = frame.xs('Close', axis=1, level=-1) if 'Close' in frame.columns.get_level_values(-1) else frame.xs('Close', axis=1, level=0)
            series = vals.iloc[:, 0]
    else:
        series = frame['Close']
    return series.dropna()


def download_and_calculate(target: date) -> dict:
    """Recupera barre recenti gratuite da Yahoo e calcola la media cash precedente.

    Yahoo limita lo storico intraday: per date vecchie non offre uno storico
    ricostruibile; l'errore è intenzionale e richiede verifica manuale.
    """
    import yfinance as yf
    now = datetime.now(ET).date()
    if target > now + timedelta(days=1):
        raise ValueError('La data dello snapshot è nel futuro')
    if target < now - timedelta(days=50):
        raise ValueError('Yahoo offre storico intraday limitato: per questa data usa la verifica manuale')
    start = target - timedelta(days=10)
    end = min(target + timedelta(days=1), now + timedelta(days=1))
    series = {}
    for symbol in ('ES=F', '^GSPC'):
        df = yf.download(symbol, start=str(start), end=str(end), interval='15m',
                         auto_adjust=False, progress=False, prepost=False, threads=False)
        series[symbol] = _close_series(df, symbol)
    result = calculate_basis(series['ES=F'], series['^GSPC'], target)
    result['source'] = 'Yahoo Finance ES=F / ^GSPC (chiusure a 15m)'
    return result

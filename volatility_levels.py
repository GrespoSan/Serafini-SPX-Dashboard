"""Confronto descrittivo tra soglie di volatilità inserite e livelli Gamma.

Nessuna stima arbitraria di sigma o Gamma Flip.
"""
from datetime import date, timedelta


def weekly_expiry(trading_day: date) -> date:
    """Regola Serafini: lun-gio venerdì corrente; ven venerdì successivo."""
    if trading_day.weekday() >= 5:
        raise ValueError('Data dello snapshot non feriale')
    this_friday = trading_day + timedelta(days=4-trading_day.weekday())
    return this_friday + timedelta(days=7) if trading_day.weekday() == 4 else this_friday


def gamma_comparison(levels: dict, offset: float, upper: float, lower: float):
    """Restituisce righe per confronto informativo, senza assegnare ruoli."""
    if offset is None:
        raise ValueError('Differenziale ES–SPX necessario')
    if not (upper > lower > 0):
        raise ValueError('Soglie di volatilità non coerenti')
    rows = []
    for label in ('C1','C2','C3','P1','P2','P3'):
        if label not in levels:
            continue
        price = float(levels[label]['strike_spx']) + float(offset)
        boundary = upper if label.startswith('C') else lower
        rows.append({'Livello': label, 'Gamma ES': round(price, 2),
                     'Soglia volatilità ES': round(boundary, 2),
                     'Differenza (Gamma − soglia)': round(price-boundary, 2),
                     'Distanza assoluta': round(abs(price-boundary), 2)})
    return rows


def breakout_reference(levels: dict, offset: float, buffer: int = 10):
    """Soglie indicative, NON ingressi automatici e NON sostituisce il contesto."""
    if offset is None:
        return []
    result=[]
    for label, step, direction in (('C1',buffer,'sopra'),('P1',-buffer,'sotto'),
                                   ('C3',buffer,'sopra'),('P3',-buffer,'sotto')):
        if label in levels:
            es = float(levels[label]['strike_spx']) + float(offset)
            result.append({'Riferimento':label,'Livello ES':round(es,2),
                           'Copertura indicativa ES':round(es+step,2),
                           'Conferma':f'Chiusura 5m {direction} {es+step:g}'})
    return result

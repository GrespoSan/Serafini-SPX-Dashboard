"""Lettura assistita delle strutture opzionali. Non modifica NET GAMMA.

Ipotesi didattica dalle slide Serafini: C1 acquistata/C2 venduta nel bull
call vertical; P1 acquistata/P2 venduta nel bear put vertical; C3 e P3
vendute dello short strangle esterno. Le posizioni NON sono deducibili
in modo certo dal solo NET GAMMA per strike.
"""
from __future__ import annotations


def build_structure(chosen, offset=None):
    """Restituisce righe e ampiezze solo se tutti i sei candidati sono validi."""
    keys = ('C1', 'C2', 'C3', 'P1', 'P2', 'P3')
    if not all(k in chosen for k in keys):
        return None
    strikes = {k: float(chosen[k]['strike_spx']) for k in keys}
    if not (strikes['C1'] < strikes['C2'] < strikes['C3']
            and strikes['P1'] > strikes['P2'] > strikes['P3']):
        raise ValueError('Ordine livelli non valido')
    roles = {
        'C1': ('Vertical Call', 'Call acquistata', 'Inizio vertical'),
        'C2': ('Vertical Call', 'Call venduta', 'Possibile target principale long'),
        'C3': ('Strangle esterno', 'Call venduta', 'Possibile target esteso long'),
        'P1': ('Vertical Put', 'Put acquistata', 'Inizio vertical'),
        'P2': ('Vertical Put', 'Put venduta', 'Possibile target principale short'),
        'P3': ('Strangle esterno', 'Put venduta', 'Possibile target esteso short'),
    }
    rows = []
    for key in keys:
        group, position, use = roles[key]
        rows.append({'Livello': key, 'Struttura ipotizzata': group,
                     'Ruolo ipotizzato': position,
                     'Strike SPX': strikes[key],
                     'Prezzo ES': None if offset is None else strikes[key] + float(offset),
                     # Gli strike simulati possono non comparire nel profilo Cboe.
                     # In quel caso il NET GAMMA e' N/D: non riusare quello
                     # del precedente strike selezionato.
                     'NET GAMMA': ('N/D' if chosen[key].get('net_gamma') is None
                                   else round(float(chosen[key]['net_gamma']), 4)),
                     'Lettura operativa': use})
    stats = {
        'Vertical Call (punti)': strikes['C2'] - strikes['C1'],
        'Vertical Put (punti)': strikes['P1'] - strikes['P2'],
        'Strangle esterno C3-P3 (punti)': strikes['C3'] - strikes['P3'],
    }
    return rows, stats


def make_study_levels(chosen, manual_strikes, profile_0dte):
    """Deriva gambe ipotetiche senza alterare i candidati Cboe originali.

    Il NET GAMMA e' letto SOLO se lo strike manuale coincide esattamente
    con uno strike presente nel profilo 0DTE della stessa fotografia.
    Valori manuali fra strike reali ottengono NET GAMMA N/D, mai inventato.
    """
    import math
    keys = ('C1', 'C2', 'C3', 'P1', 'P2', 'P3')
    if not all(key in chosen and key in manual_strikes for key in keys):
        raise ValueError('Servono tutti e sei i livelli per la simulazione.')
    strikes = {key: float(manual_strikes[key]) for key in keys}
    if not all(math.isfinite(value) and value > 0 for value in strikes.values()):
        raise ValueError('Gli strike manuali devono essere numeri positivi e finiti.')
    if not (strikes['C1'] < strikes['C2'] < strikes['C3'] and
            strikes['P1'] > strikes['P2'] > strikes['P3']):
        raise ValueError('Ordine non valido: C1 < C2 < C3 e P1 > P2 > P3.')
    rows = (profile_0dte or {}).get('rows') or []
    gamma_by_strike = {
        round(float(row['strike_spx']), 6): float(row['net_gamma'])
        for row in rows
        if math.isfinite(float(row['strike_spx']))
        and math.isfinite(float(row['net_gamma']))
    }
    # Copia nuova e separata, NON modifica chosen, candidati o NET GAMMA.
    return {
        key: {'strike_spx': value,
              'net_gamma': gamma_by_strike.get(round(value, 6))}
        for key, value in strikes.items()
    }


def future_es_to_spx_strikes(future_prices, offset):
    """Converti livelli letti sul future ES in strike SPX ipotetici.

    La conversione avviene SOLO nel laboratorio di studio. NET GAMMA Cboe,
    selezione automatica e livelli salvati restano sul sottostante SPX.
    Il widget usa step di 0,25; non arrotondare automaticamente i valori
    convertiti perché il differenziale ES−SPX può essere decimale.
    """
    import math
    keys = ('C1', 'C2', 'C3', 'P1', 'P2', 'P3')
    if not all(key in future_prices for key in keys):
        raise ValueError('Servono tutti e sei i prezzi ES.')
    if offset is None or not math.isfinite(float(offset)):
        raise ValueError('Differenziale ES − SPX non disponibile.')
    basis = float(offset)
    converted = {}
    for key in keys:
        price = float(future_prices[key])
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f'{key}: prezzo ES non valido.')
        strike = round(price - basis, 6)
        if strike <= 0:
            raise ValueError(f'{key}: strike SPX convertito non valido.')
        converted[key] = strike
    return converted

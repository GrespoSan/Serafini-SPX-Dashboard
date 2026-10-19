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
                     'NET GAMMA': round(float(chosen[key]['net_gamma']), 4),
                     'Lettura operativa': use})
    stats = {
        'Vertical Call (punti)': strikes['C2'] - strikes['C1'],
        'Vertical Put (punti)': strikes['P1'] - strikes['P2'],
        'Strangle esterno C3-P3 (punti)': strikes['C3'] - strikes['P3'],
    }
    return rows, stats

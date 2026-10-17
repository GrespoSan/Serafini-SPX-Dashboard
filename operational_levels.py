"""Selezione SPERIMENTALE delle tre barre NET GAMMA più intense per lato.

Il NET GAMMA è calcolato altrove e NON viene modificato.
Regola di prova: tra i 25 strike sopra la chiusura SPX si ordinano solo i
NET GAMMA positivi per valore decrescente; tra i 25 sotto si ordinano solo i
NET GAMMA negativi per valore assoluto decrescente. Nessun confronto con
strike adiacenti, nessuna soglia aggiuntiva, nessun raggruppamento.
Il criterio resta una ipotesi di riconoscimento, non una regola numerica
esplicitamente confermata da Serafini.
"""
from __future__ import annotations

import csv
import io


def find_candidates(rows, reference):
    """Ritorna le barre eleggibili, ordinate per intensità di gamma per lato."""
    items = list(rows)
    strikes = [float(r['strike_spx']) for r in items]
    if len(set(strikes)) != len(strikes):
        raise ValueError('Strike duplicati nel profilo')
    calls = []
    puts = []
    for r in items:
        strike = float(r['strike_spx'])
        gamma = float(r['net_gamma'])
        item = {'strike_spx': strike, 'net_gamma': gamma, 'magnitude': abs(gamma)}
        if strike > reference and gamma > 0:
            calls.append(item)
        elif strike < reference and gamma < 0:
            puts.append(item)
    calls.sort(key=lambda x: (-x['magnitude'], x['strike_spx']))
    puts.sort(key=lambda x: (-x['magnitude'], -x['strike_spx']))
    return {'call': calls, 'put': puts}


def proposed_levels(candidates, number=3):
    """Seleziona i primi tre per intensità e assegna C1→C3/P1→P3 per prezzo."""
    calls = sorted(candidates['call'][:number], key=lambda x: x['strike_spx'])
    puts = sorted(candidates['put'][:number], key=lambda x: -x['strike_spx'])
    return {**{f'C{i}': item for i, item in enumerate(calls, 1)},
            **{f'P{i}': item for i, item in enumerate(puts, 1)}}


def candidate_csv(candidates, offset=None):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['lato', 'strike_spx', 'prezzo_es', 'net_gamma', 'ranking_intensita', 'selezionato_top3'])
    for side in ('call', 'put'):
        for i, item in enumerate(candidates[side], 1):
            writer.writerow([side.upper(), item['strike_spx'],
                             '' if offset is None else item['strike_spx'] + offset,
                             item['net_gamma'], i, 'SI' if i <= 3 else 'NO'])
    return buffer.getvalue().encode('utf-8-sig')

"""Confronto NET GAMMA tra due snapshot Cboe, per gli stessi strike SPX.
Non modifica il calcolo NET GAMMA e non assegna direzioni operative.
"""
import csv
import io
import math


def compare_profiles(morning, afternoon, basis=None):
    """Confronta esclusivamente strike presenti in entrambi i profili.

    Preserva gli strike SPX, anche quando i 25+25 differiscono. Per l'ES si
    applica un solo differenziale selezionato dall'utente.
    La percentuale e' relativa al valore assoluto NET GAMMA del mattino;
    e' indefinita se il valore iniziale e' zero. Per il rafforzamento del
    segno si usa la variazione di valore assoluto.
    """
    a = {float(x['strike_spx']): float(x['net_gamma']) for x in morning['rows']}
    b = {float(x['strike_spx']): float(x['net_gamma']) for x in afternoon['rows']}
    common = sorted(set(a) & set(b))
    rows = []
    for strike in common:
        old, new = a[strike], b[strike]
        delta = new - old
        magnitude_delta = abs(new) - abs(old)
        sign_old = 1 if old > 0 else -1 if old < 0 else 0
        sign_new = 1 if new > 0 else -1 if new < 0 else 0
        if sign_old != sign_new:
            event = 'Cambio segno' if sign_old and sign_new else 'Passaggio da/a zero'
        elif magnitude_delta > 1e-9:
            event = 'Rafforzamento intensità'
        elif magnitude_delta < -1e-9:
            event = 'Indebolimento intensità'
        else:
            event = 'Invariato'
        rows.append({
            'Strike SPX': strike,
            'Prezzo ES': None if basis is None else strike + basis,
            'NET mattina': old,
            'NET pomeriggio': new,
            'Delta NET': delta,
            'Delta intensità': magnitude_delta,
            'Delta intensità %': None if abs(old) < 1e-12 else 100 * magnitude_delta / abs(old),
            'Evento': event,
        })
    return rows, {'comuni': len(common), 'solo_mattina': len(set(a) - set(b)),
                  'solo_pomeriggio': len(set(b) - set(a)),
                  'cambi_segno': sum(r['Evento'] == 'Cambio segno' for r in rows)}


def as_csv(rows):
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')

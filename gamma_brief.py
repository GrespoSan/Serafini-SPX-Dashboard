"""Sintesi visuale dei due snapshot: nessun nuovo calcolo NET GAMMA."""


def select_key_levels(rows, count=5, minimum_separation=10.0):
    """Sceglie le maggiori intensità osservate nei due snapshot.

    Evita di riempire il grafico con strike immediatamente vicini; è una
    regola di impaginazione, NON una selezione C1/C2/C3 o supporti.
    """
    ranked = sorted(rows, key=lambda r: (-max(abs(r['NET mattina']), abs(r['NET pomeriggio'])), r['Strike SPX']))
    chosen = []
    for row in ranked:
        if all(abs(row['Strike SPX'] - old['Strike SPX']) >= minimum_separation for old in chosen):
            chosen.append(row)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda r: r['Strike SPX'])


def summary(rows):
    """Descrive soltanto il cambio di intensità, non un segnale di trading."""
    if not rows:
        return 'Nessun livello confrontabile.'
    weaker = [r for r in rows if abs(r['NET pomeriggio']) < abs(r['NET mattina']) - 1e-8]
    stronger = [r for r in rows if abs(r['NET pomeriggio']) > abs(r['NET mattina']) + 1e-8]
    fmt = lambda r: f"{r['Prezzo ES']:,.0f}".replace(',', '.') if r['Prezzo ES'] is not None else f"SPX {r['Strike SPX']:g}"
    if weaker and not stronger:
        return 'Le concentrazioni mostrate si sono tutte indebolite; soprattutto ' + ', '.join(fmt(r) for r in sorted(weaker, key=lambda r: r['Delta intensità'])[:2]) + '.'
    if stronger and not weaker:
        return 'Le concentrazioni mostrate si sono tutte rafforzate; soprattutto ' + ', '.join(fmt(r) for r in sorted(stronger, key=lambda r: -r['Delta intensità'])[:2]) + '.'
    if weaker and stronger:
        w = min(weaker, key=lambda r: r['Delta intensità'])
        s = max(stronger, key=lambda r: r['Delta intensità'])
        return f'Movimento misto: maggiore indebolimento a {fmt(w)}; maggiore rafforzamento a {fmt(s)}.'
    return 'Le intensità NET GAMMA mostrate sono sostanzialmente invariate.'

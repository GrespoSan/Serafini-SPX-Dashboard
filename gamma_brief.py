"""Sintesi visuale dei due snapshot: nessun nuovo calcolo NET GAMMA."""


def select_key_levels(rows, count_per_sign=4, minimum_separation=5.0):
    """Fino a quattro strike positivi e quattro negativi, secondo il NET mattutino.

    Il segno mattutino decide il gruppo per evitare che uno strike che cambia
    segno sia classificato retroattivamente usando i dati pomeridiani.
    I cambi di segno vengono segnalati separatamente su TUTTI gli strike.
    Nessuna attribuzione automatica di Call/Put o supporti/resistenze.
    """
    chosen = []
    for side in (1, -1):
        eligible = [r for r in rows if (r['NET mattina'] > 0 if side == 1 else r['NET mattina'] < 0)]
        ranked = sorted(
            eligible,
            key=lambda r: (-max(abs(r['NET mattina']), abs(r['NET pomeriggio'])), r['Strike SPX']),
        )
        selected = []
        for r in ranked:
            if all(abs(r['Strike SPX'] - old['Strike SPX']) >= minimum_separation for old in selected):
                selected.append(r)
            if len(selected) >= count_per_sign:
                break
        chosen.extend(selected)
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


def operational_summary(rows):
    """Descrizione verificabile degli 8 livelli, senza dedurre supporti o segnali dal segno Gamma."""
    def fmt(r):
        v = r['Prezzo ES']
        return f"{v:,.0f}".replace(',', '.') if v is not None else f"SPX {r['Strike SPX']:g}"
    def summarize_group(group):
        weaker = sorted((r for r in group if abs(r['NET pomeriggio']) < abs(r['NET mattina']) - 1e-8),
                        key=lambda r: abs(r['NET mattina']) - abs(r['NET pomeriggio']), reverse=True)
        stronger = sorted((r for r in group if abs(r['NET pomeriggio']) > abs(r['NET mattina']) + 1e-8),
                          key=lambda r: abs(r['NET pomeriggio']) - abs(r['NET mattina']), reverse=True)
        parts = []
        if weaker:
            parts.append('indeboliti: ' + ', '.join(fmt(r) for r in weaker[:2]))
        if stronger:
            parts.append('rafforzati: ' + ', '.join(fmt(r) for r in stronger[:2]))
        return '; '.join(parts) if parts else 'invariati'
    positive = [r for r in rows if r['NET mattina'] > 0 and r['NET pomeriggio'] > 0]
    negative = [r for r in rows if r['NET mattina'] < 0 and r['NET pomeriggio'] < 0]
    mixed = [r for r in rows if r['NET mattina'] * r['NET pomeriggio'] <= 0]
    lines = []
    if positive:
        lines.append('**Gamma positivo:** ' + summarize_group(positive) + '.')
    if negative:
        lines.append('**Gamma negativo:** ' + summarize_group(negative) + '.')
    if mixed:
        lines.append('**Segno cambiato (fra gli 8):** ' + ', '.join(fmt(r) for r in mixed) + '.')
    return lines if lines else ['Nessuna variazione rilevante sui livelli selezionati.']


def transition_zones(rows, max_zones=3):
    """Rileva gruppi CONTIGUI di strike che cambiano segno fra due snapshot.

    Espande ogni gruppo di un solo strike su ogni lato per fornire contesto.
    I livelli esterni non sono automaticamente supporti, breakout o gamma flip.
    """
    ordered = sorted(rows, key=lambda r: float(r['Strike SPX']))
    if not ordered:
        return []
    gaps = [float(b['Strike SPX']) - float(a['Strike SPX']) for a, b in zip(ordered, ordered[1:])]
    positive_gaps = sorted(g for g in gaps if g > 0)
    step = positive_gaps[len(positive_gaps)//2] if positive_gaps else 5.0
    changed = [i for i, r in enumerate(ordered) if (r['NET mattina'] > 0 > r['NET pomeriggio']
                                                      or r['NET mattina'] < 0 < r['NET pomeriggio'])]
    if not changed:
        return []
    groups = []
    for idx in changed:
        if groups and idx == groups[-1][-1] + 1 and gaps[idx - 1] <= step * 1.01:
            groups[-1].append(idx)
        else:
            groups.append([idx])
    result = []
    for indices in groups:
        left, right = indices[0], indices[-1]
        start = left - 1 if left > 0 and gaps[left-1] <= step*1.01 else left
        end = right
        for _ in range(2):
            if end + 1 < len(ordered) and gaps[end] <= step * 1.01:
                end += 1
        context = ordered[start:end+1]
        direction = ('verso NET negativo' if all(ordered[i]['NET mattina'] > 0 > ordered[i]['NET pomeriggio'] for i in indices)
                     else 'verso NET positivo' if all(ordered[i]['NET mattina'] < 0 < ordered[i]['NET pomeriggio'] for i in indices)
                     else 'mista')
        result.append({'rows': context, 'changed_count': len(indices),
                       'direction': direction, 'indices': indices,
                       'score': sum(abs(ordered[i]['NET pomeriggio'] - ordered[i]['NET mattina']) for i in indices)})
    result.sort(key=lambda z: (-z['changed_count'], -z['score']))
    return result[:max_zones]

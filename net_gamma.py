"""NET GAMMA come definito nella slide 132 di Serafini.
Non e' una misura monetaria di dealer GEX. File Cboe originali non modificati.
"""
import gzip
import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta

CONTRACT = re.compile(r'^SPX(W?)(\d{6})([CP])(\d{8})$')


def read_snapshot(blob: bytes):
    data = json.loads(gzip.decompress(blob))
    if data.get('symbol') not in ('SPX', '_SPX') or not isinstance(data.get('data', {}).get('options'), list):
        raise ValueError('Snapshot SPX Cboe non valido')
    return data


def parse_contract(code: str):
    match = CONTRACT.fullmatch(code)
    if not match:
        return None
    _, yymmdd, side, strike = match.groups()
    return date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])), side, int(strike) / 1000


def friday_of(d: date) -> date:
    return d + timedelta(days=4 - d.weekday())


def profile(snapshot: dict, expiry: date, reference: float, strike_count: int = 50):
    """Regola confermata da Serafini: 25 strike SOPRA e 25 SOTTO la chiusura SPX.
    Lo strike esattamente uguale al riferimento resta escluso: 25+25=50.
    Utilizza gli strike effettivamente disponibili per la scadenza richiesta.
    """
    if strike_count != 50:
        raise ValueError('Il metodo Serafini richiede esattamente 50 strike (25 sopra e 25 sotto)')
    if not reference > 0:
        raise ValueError('Chiusura SPX precedente obbligatoria')
    raw = defaultdict(lambda: {'call_oi': 0., 'call_vol': 0., 'put_oi': 0., 'put_vol': 0., 'call_weighted': 0., 'put_weighted': 0., 'call_n': 0, 'put_n': 0})
    rows = 0
    for option in snapshot['data']['options']:
        parts = parse_contract(option.get('option', ''))
        if parts is None or parts[0] != expiry:
            continue
        _, side, strike = parts
        gamma = float(option.get('gamma') or 0)
        oi = float(option.get('open_interest') or 0)
        vol = float(option.get('volume') or 0)
        if not (0 <= oi and 0 <= vol and 0 <= gamma):
            raise ValueError('OI, volume o gamma negativo in un contratto')
        row = raw[strike]
        pre = 'call' if side == 'C' else 'put'
        row[f'{pre}_oi'] += oi
        row[f'{pre}_vol'] += vol
        row[f'{pre}_weighted'] += (oi + vol) * gamma
        row[f'{pre}_n'] += 1
        rows += 1
    if not raw:
        return {'expiry': expiry.isoformat(), 'contracts': 0, 'available_strikes': 0, 'rows': [], 'selection': '25 strike sopra + 25 sotto la chiusura SPX'}
    below = sorted((s for s in raw if s < reference), reverse=True)[:25]
    above = sorted((s for s in raw if s > reference))[:25]
    if len(below) != 25 or len(above) != 25:
        raise ValueError(f'Strike insufficienti: {len(below)} sotto e {len(above)} sopra; occorrono 25 per lato. Nessun profilo incompleto viene esportato.')
    selected = set(below + above)
    records = []
    for k in sorted(selected):
        z = raw[k]
        records.append({'strike_spx': k, 'call_oi': z['call_oi'], 'call_volume': z['call_vol'],
                        'put_oi': z['put_oi'], 'put_volume': z['put_vol'],
                        'call_gamma_component': z['call_weighted'], 'put_gamma_component': z['put_weighted'],
                        'net_gamma': z['call_weighted'] - z['put_weighted'],
                        'call_contracts': z['call_n'], 'put_contracts': z['put_n']})
    return {'expiry': expiry.isoformat(), 'contracts': rows, 'available_strikes': len(raw), 'rows': records,
            'selection': '25 strike sopra + 25 sotto la chiusura SPX (conferma diretta Serafini)'}


def profile_csv(result, offset=None):
    import csv
    out = io.StringIO(); columns = ['strike_spx', 'strike_es', 'net_gamma', 'call_gamma_component',
                                      'put_gamma_component', 'call_oi', 'call_volume', 'put_oi', 'put_volume']
    writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
    for row in result['rows']:
        writer.writerow({**{k: row.get(k, '') for k in columns}, 'strike_es': '' if offset is None else row['strike_spx'] + offset})
    return out.getvalue().encode('utf-8-sig')

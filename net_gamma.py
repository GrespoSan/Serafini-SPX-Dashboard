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



def visual_profile(snapshot: dict, base: dict, expiry: date, reference: float,
                   spot=None, buffer_pct: float = 0.025, max_strikes: int = 140):
    """Estensione solo grafica: conserva 25+25 originali e NET GAMMA identici.

    Seleziona dalla STESSA catena Cboe soltanto strike ulteriori oltre i
    due estremi dei 50 originali. Il margine ±2,5% dal prezzo Cboe fotografato
    non è un prezzo live; lo storico non viene alterato o ricostruito.
    Cap di 140 righe/profilo per Pine comparativo a tre profili (max 500 linee).
    """
    from math import isfinite
    core = list(base.get("rows") or [])
    if len(core) != 50 or len({float(r["strike_spx"]) for r in core}) != 50:
        raise ValueError("L'estensione richiede 25+25 strike Serafini validi")
    if not (isfinite(float(reference)) and reference > 0):
        raise ValueError("Riferimento SPX non valido")
    if not (0 < float(buffer_pct) <= 0.1 and 50 <= max_strikes <= 140):
        raise ValueError("Estensione: parametri di copertura non validi")
    try:
        spot_value = float(spot)
        if not isfinite(spot_value) or spot_value <= 0:
            spot_value = float(reference)
    except (ValueError, TypeError, OverflowError):
        spot_value = float(reference)
    baseline_min = min(float(r["strike_spx"]) for r in core)
    baseline_max = max(float(r["strike_spx"]) for r in core)
    requested_low = spot_value - float(buffer_pct) * reference
    requested_high = spot_value + float(buffer_pct) * reference
    core_strikes = {float(r["strike_spx"]) for r in core}
    # Non estendere verso il lato gia' coperto. Non usare prezzi futuri:
    # focus e margine sono congelati nel momento del rilevamento Cboe.
    side_low = requested_low < baseline_min
    side_high = requested_high > baseline_max
    extra = {}
    if side_low or side_high:
        for option in snapshot["data"]["options"]:
            parsed = parse_contract(str(option.get("option", "")))
            if parsed is None or parsed[0] != expiry:
                continue
            _, side, strike = parsed
            if strike in core_strikes:
                continue
            if not ((side_low and requested_low <= strike < baseline_min) or
                    (side_high and baseline_max < strike <= requested_high)):
                continue
            gamma = float(option.get("gamma") or 0)
            oi = float(option.get("open_interest") or 0)
            vol = float(option.get("volume") or 0)
            if not all(isfinite(v) and v >= 0 for v in (gamma, oi, vol)):
                raise ValueError("Gamma/OI/volume non validi nell'estensione")
            pair = extra.setdefault(strike, [0.0, 0.0])
            pair[0 if side == "C" else 1] += (oi + vol) * gamma
    # Se oltre il limite scegliamo per prima la direzione del movimento;
    # su ciascun lato manteniamo gli strike CONTIGUI dal bordo Serafini.
    lower = sorted((k for k in extra if k < baseline_min), reverse=True)
    upper = sorted((k for k in extra if k > baseline_max))
    available = max_strikes - len(core)
    chosen = []
    if spot_value < reference:
        sides = (lower, upper)
    else:
        sides = (upper, lower)
    for side in sides:
        take = min(available, len(side))
        chosen.extend(side[:take])
        available -= take
    final = core + [
        {"strike_spx": strike,
         "call_gamma_component": extra[strike][0],
         "put_gamma_component": extra[strike][1],
         "net_gamma": extra[strike][0] - extra[strike][1]}
        for strike in chosen
    ]
    final.sort(key=lambda r: float(r["strike_spx"]))
    # Copertura misurata soltanto sui livelli disponibili nello snapshot:
    # non affermare che una fascia è coperta quando la catena è insufficiente.
    actual_low = float(final[0]["strike_spx"])
    actual_high = float(final[-1]["strike_spx"])
    complete = ((not side_low or actual_low <= requested_low) and
                (not side_high or actual_high >= requested_high))
    return {
        "expiry": expiry.isoformat(), "rows": final,
        "core_strikes": len(core), "extra_strikes": len(final) - len(core),
        "reference_spx": float(reference), "capture_spot_spx": spot_value,
        "requested_low": requested_low, "requested_high": requested_high,
        "coverage_complete": complete, "max_strikes": max_strikes,
        "source": "Stessa fotografia Cboe, estensione grafica non Serafini",
    }


def profile_csv(result, offset=None):
    import csv
    out = io.StringIO(); columns = ['strike_spx', 'strike_es', 'net_gamma', 'call_gamma_component',
                                      'put_gamma_component', 'call_oi', 'call_volume', 'put_oi', 'put_volume']
    writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
    for row in result['rows']:
        writer.writerow({**{k: row.get(k, '') for k in columns}, 'strike_es': '' if offset is None else row['strike_spx'] + offset})
    return out.getvalue().encode('utf-8-sig')

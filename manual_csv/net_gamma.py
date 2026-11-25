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
                   spot=None, buffer_pct: float | None = None,
                   max_strikes: int = 200, known_low=None, known_high=None):
    """Estensione INTELLIGENTE, esclusivamente grafica, dagli strike reali Cboe.

    Prima verifica se i 50 strike Serafini coprono la fascia dei prezzi
    conosciuti ex ante con un piccolo margine. Se sì restituisce solo i 50
    originali, senza nemmeno scansionare gli altri contratti. Se no include
    strike solo sul lato (o sui lati) realmente scoperto, al massimo 200.

    Margine adattivo: massimo tra 0,25% della chiusura precedente,
    due passi mediani della griglia strike e 20% dell'escursione degli
    ancoraggi verificati; limite superiore 1% del riferimento.
    Il parametro buffer_pct esplicito conserva la modalità a margine
    fisso per confronti metodologici e test regressivi.
    """
    from math import isfinite

    core = list(base.get("rows") or [])
    core_strikes = {float(r["strike_spx"]) for r in core}
    if len(core) != 50 or len(core_strikes) != 50:
        raise ValueError("L'estensione richiede i 50 strike Serafini 25+25 invariati")
    if not isfinite(float(reference)) or reference <= 0:
        raise ValueError("Riferimento SPX non valido")
    if not (isinstance(max_strikes, int) and 50 <= max_strikes <= 200):
        raise ValueError("Massimo strike grafici consentito: 50–200")
    if buffer_pct is not None and (
            not isfinite(float(buffer_pct))
            or not 0 < float(buffer_pct) <= .10):
        raise ValueError("Margine grafico esplicito non consentito")

    reference = float(reference)
    try:
        spot_value = float(spot)
        if not isfinite(spot_value) or spot_value <= 0:
            spot_value = reference
    except (ValueError, TypeError, OverflowError):
        spot_value = reference

    anchors = [reference, spot_value]
    for bound in (known_low, known_high):
        if bound is None:
            continue
        value = float(bound)
        if not isfinite(value) or value <= 0:
            raise ValueError("Estremo storico dell'estensione non valido")
        anchors.append(value)

    ordered_core = sorted(core_strikes)
    grid_steps = sorted(
        hi - lo for lo, hi in zip(ordered_core, ordered_core[1:])
        if hi > lo
    )
    grid_step = grid_steps[len(grid_steps) // 2]
    if buffer_pct is None:
        anchor_span = max(anchors) - min(anchors)
        margin = min(
            max(reference * .0025, 2.0 * grid_step, .20 * anchor_span),
            reference * .01,
        )
        band_mode = "adattivo"
    else:
        margin = reference * float(buffer_pct)
        band_mode = "fisso"
    requested_low = max(min(anchors) - margin, min(anchors) * .90)
    requested_high = max(anchors) + margin

    baseline_min = ordered_core[0]
    baseline_max = ordered_core[-1]
    need_low = requested_low < baseline_min
    need_high = requested_high > baseline_max

    # In una giornata normale il Pine con showExtension=true deve essere
    # visivamente IDENTICO al 25+25. Non analizzare le altre 32k opzioni.
    if not (need_low or need_high) or max_strikes == 50:
        sufficient = not (need_low or need_high)
        return {
            "expiry": expiry.isoformat(),
            "rows": core,
            "core_strikes": 50,
            "extra_strikes": 0,
            "reference_spx": reference,
            "capture_spot_spx": spot_value,
            "requested_low": requested_low,
            "requested_high": requested_high,
            "actual_low": baseline_min,
            "actual_high": baseline_max,
            "coverage_complete": sufficient,
            "source_strikes_in_range": 50,
            "sampled_strikes": 0,
            "max_strikes": max_strikes,
            "band_mode": band_mode,
            "margin_points": margin,
            "need_low": need_low,
            "need_high": need_high,
            "decision": "50_sufficienti" if sufficient else "limite_50",
            "source": "Stessa fotografia Cboe; solo rappresentazione grafica",
        }

    extra = {}
    for option in snapshot["data"]["options"]:
        parsed = parse_contract(str(option.get("option", "")))
        if parsed is None or parsed[0] != expiry:
            continue
        _, side, strike = parsed
        if strike in core_strikes:
            continue
        if not ((need_low and requested_low <= strike < baseline_min)
                or (need_high and baseline_max < strike <= requested_high)):
            continue
        gamma = float(option.get("gamma") or 0)
        oi = float(option.get("open_interest") or 0)
        volume = float(option.get("volume") or 0)
        if not all(isfinite(v) and v >= 0 for v in (gamma, oi, volume)):
            raise ValueError("Gamma/OI/Volume Cboe non valido nell'estensione")
        pair = extra.setdefault(strike, [0.0, 0.0])
        pair[0 if side == "C" else 1] += (oi + volume) * gamma

    lower = sorted(k for k in extra if k < baseline_min)
    upper = sorted(k for k in extra if k > baseline_max)
    room = max_strikes - len(core)
    if room and lower and upper:
        distance_low = baseline_min - requested_low
        distance_high = requested_high - baseline_max
        low_quota = min(len(lower), max(1, min(room - 1, round(
            room * distance_low / max(distance_low + distance_high, 1e-9)
        ))))
        high_quota = min(len(upper), room - low_quota)
        unused = room - low_quota - high_quota
        for side in ("lower", "upper"):
            if unused <= 0:
                break
            if side == "lower":
                added = min(unused, len(lower) - low_quota)
                low_quota += added
            else:
                added = min(unused, len(upper) - high_quota)
                high_quota += added
            unused -= added
    elif lower:
        low_quota, high_quota = min(len(lower), room), 0
    else:
        low_quota, high_quota = 0, min(len(upper), room)

    def _select_real_strikes(side, capacity):
        if capacity <= 0 or not side:
            return []
        if len(side) <= capacity:
            return list(side)
        # Distribuzione lungo tutto l'intervallo richiesto. Manteniamo
        # gli estremi e circa un terzo dei NET GAMMA più intensi,
        # senza aggregare o creare strike sintetici.
        selected = {side[0], side[-1]} if capacity >= 2 else {side[0]}
        strong = sorted(side, key=lambda k:
                        (-abs(extra[k][0] - extra[k][1]), k))
        strong_slots = min(max(0, capacity - len(selected)), capacity // 3)
        for strike in strong:
            if len(selected) >= strong_slots + 2 or len(selected) >= capacity:
                break
            selected.add(strike)
        if len(selected) < capacity:
            # Scegliere valori realmente presenti, distribuiti uniformemente
            # rispetto al PREZZO, non alla densità di quotazione Cboe.
            lo, hi = side[0], side[-1]
            needed = capacity - len(selected)
            for i in range(1, needed + 1):
                desired = lo + (hi - lo) * i / (needed + 1)
                candidate = min((x for x in side if x not in selected),
                                key=lambda x: (abs(x - desired), x))
                selected.add(candidate)
        return sorted(selected)

    chosen = _select_real_strikes(lower, low_quota) + _select_real_strikes(
        upper, high_quota
    )
    final = core + [
        {"strike_spx": k, "call_gamma_component": extra[k][0],
         "put_gamma_component": extra[k][1],
         "net_gamma": extra[k][0] - extra[k][1]}
        for k in chosen
    ]
    final.sort(key=lambda row: float(row["strike_spx"]))
    actual_low = float(final[0]["strike_spx"])
    actual_high = float(final[-1]["strike_spx"])
    steps = [b - a for a, b in zip(sorted(core_strikes), sorted(core_strikes)[1:])]
    tolerance = min(steps) if steps else 0.0
    coverage_complete = (
        (not need_low or actual_low <= requested_low + tolerance)
        and (not need_high or actual_high >= requested_high - tolerance)
    )
    return {
        "expiry": expiry.isoformat(),
        "rows": final,
        "core_strikes": len(core),
        "extra_strikes": len(final) - len(core),
        "reference_spx": float(reference),
        "capture_spot_spx": spot_value,
        "requested_low": requested_low,
        "requested_high": requested_high,
        "actual_low": actual_low,
        "actual_high": actual_high,
        "coverage_complete": coverage_complete,
        "source_strikes_in_range": len(extra) + len(core),
        "sampled_strikes": max(0, len(extra) - len(chosen)),
        "max_strikes": max_strikes,
        "band_mode": band_mode,
        "margin_points": margin,
        "need_low": need_low,
        "need_high": need_high,
        "decision": ("estesa_entrambi" if need_low and need_high else
                     "estesa_sotto" if need_low else "estesa_sopra"),
        "source": "Stessa fotografia Cboe; estensione solo grafica, non Serafini",
    }



def gamma_component_totals(profiles, *, require_core=False):
    """Somme delle stesse righe richieste, senza estendere l'universo implicitamente.

    Restituisce le componenti Serafini call e put (entrambe positive prima
    della sottrazione) e il NET GAMMA. Il NET principale usa require_core=True:
    ogni scadenza presente deve avere esattamente 25+25 righe.
    La stessa funzione può documentare, separatamente, la somma puramente
    grafica degli strike estesi.
    """
    from math import fsum, isfinite, isclose
    rows = []
    included = 0
    for group in profiles:
        items = list(group.get("rows") or [])
        if not items:
            continue
        if require_core and (len(items) != 50 or len({
                float(item["strike_spx"]) for item in items}) != 50):
            raise ValueError("Totale Serafini: attesi 25+25 strike per scadenza")
        rows.extend(items)
        included += 1
    if not rows:
        return None
    calls = [float(row["call_gamma_component"]) for row in rows]
    puts = [float(row["put_gamma_component"]) for row in rows]
    nets = [float(row["net_gamma"]) for row in rows]
    if not all(isfinite(v) for v in calls + puts + nets):
        raise ValueError("Somma NET GAMMA non finita")
    net = fsum(nets)
    call = fsum(calls)
    put = fsum(puts)
    if not isclose(net, call - put, rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError("NET GAMMA diverso da Call - Put sulle righe selezionate")
    return {"net": net, "call": call, "put": put, "strikes": len(rows),
            "profiles": included}



def profile_csv(result, offset=None):
    import csv
    out = io.StringIO(); columns = ['strike_spx', 'strike_es', 'net_gamma', 'call_gamma_component',
                                      'put_gamma_component', 'call_oi', 'call_volume', 'put_oi', 'put_volume']
    writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
    for row in result['rows']:
        writer.writerow({**{k: row.get(k, '') for k in columns}, 'strike_es': '' if offset is None else row['strike_spx'] + offset})
    return out.getvalue().encode('utf-8-sig')

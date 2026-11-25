"""NDX/NDXP exploratory NET GAMMA, without changing the SPX project.

Model explicitly copied as a *study hypothesis* from Serafini's SPX method:
  call (OI + volume) * gamma; put (OI + volume) * gamma; net call - put.
Gamma values from the Cboe payload are not scaled into monetary dealer exposure.
The cross-market method remains exploratory. Strike selection is a symmetric
percentage band around the previous NDX close, common to 0DTE and Weekly.
"""
import gzip
import json
import math
import re
from collections import defaultdict
from datetime import date, timedelta

DEFAULT_BAND_PCT = 3.0  # banda simmetrica iniziale per 0DTE e Weekly

NDX_CONTRACT = re.compile(r'^(NDX|NDXP|NDXW)(\d{6})([CP])(\d{8})$')


def unpack_snapshot(blob: bytes) -> dict:
    if blob.startswith(b'\x1f\x8b'):
        blob = gzip.decompress(blob)
    doc = json.loads(blob)
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), dict):
        raise ValueError('File Cboe non valido: oggetto data assente.')
    if not isinstance(doc['data'].get('options'), list):
        raise ValueError('File Cboe non valido: catena opzioni assente.')
    symbol = str(doc.get('symbol', '')).upper().lstrip('_')
    if symbol != 'NDX':
        raise ValueError(f'Atteso NDX, ricevuto {symbol or "simbolo vuoto"}. Non caricare catene SPX.')
    return doc


def contract_parts(code: str):
    # OCC-style with spaces is stripped, but unrecognized roots are never accepted.
    match = NDX_CONTRACT.fullmatch(str(code or '').strip().replace(' ', '').upper())
    if not match:
        return None
    _, yymmdd, side, raw_strike = match.groups()
    try:
        expiry = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
    except ValueError:
        return None
    return expiry, side, int(raw_strike) / 1000.0


def available_expirations(doc: dict) -> list[date]:
    return sorted({p[0] for o in doc['data']['options'] if (p := contract_parts(o.get('option',''))) is not None})


def weekly_target(day: date, available: list[date]) -> date | None:
    """Choose the next actually present Friday; on Friday choose the next one.
    A missing weekly is NOT replaced with an arbitrary expiry.
    """
    delta = (4 - day.weekday()) % 7
    if delta == 0:
        delta = 7
    friday = day + timedelta(days=delta)
    return friday if friday in available else None


def close_from_payload(doc: dict) -> float | None:
    raw = doc.get('data',{}).get('prev_day_close')
    try:
        value = float(raw)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _nonnegative(value, field):
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f'{field} non numerico')
    if not math.isfinite(v) or v < 0:
        raise ValueError(f'{field} non valido: negativo o non finito')
    return v


def profile(doc: dict, expiry: date, reference: float, band_pct: float = DEFAULT_BAND_PCT) -> dict:
    """Use every real strike within +/- band_pct% of the previous index close.

    The same absolute price window is applied to both expiries. No 25+25
    cap, synthetic strikes, interpolation or changes to Gamma calculation.
    """
    if not math.isfinite(float(reference)) or reference <= 0:
        raise ValueError('Chiusura NDX precedente valida obbligatoria.')
    raw = defaultdict(lambda: {'call_weighted': 0.0, 'put_weighted': 0.0,
                               'call_oi': 0.0, 'put_oi': 0.0,
                               'call_volume': 0.0, 'put_volume': 0.0})
    matched = 0
    usable_gamma = 0
    for opt in doc['data']['options']:
        parts = contract_parts(opt.get('option'))
        if parts is None or parts[0] != expiry:
            continue
        _, side, strike = parts
        gamma = _nonnegative(opt.get('gamma'), 'Gamma')
        oi = _nonnegative(opt.get('open_interest'), 'Open Interest')
        volume = _nonnegative(opt.get('volume'), 'Volume')
        if gamma > 0:
            usable_gamma += 1
        part = 'call' if side == 'C' else 'put'
        row = raw[strike]
        row[f'{part}_weighted'] += (oi + volume) * gamma
        row[f'{part}_oi'] += oi
        row[f'{part}_volume'] += volume
        matched += 1
    if usable_gamma == 0:
        raise ValueError(f'{expiry:%d/%m/%Y}: Gamma tutti zero/assenti. Profilo non utilizzabile.')
    if not math.isfinite(band_pct) or not (0 < band_pct <= 100):
        raise ValueError('Ampiezza percentuale non valida.')
    # Evita errori floating-point proprio sui due strike di confine.
    lower = round(reference * (1 - band_pct / 100.0), 10)
    upper = round(reference * (1 + band_pct / 100.0), 10)
    selected = sorted(k for k in raw if lower <= k <= upper)
    if not selected:
        raise ValueError(
            f'{expiry:%d/%m/%Y}: nessuno strike disponibile nella fascia '
            f'{lower:,.2f}–{upper:,.2f} ({band_pct:g}%).'
        )
    records=[]
    for strike in selected:
        row=raw[strike]
        records.append({'strike_index': strike,
                        'call_gamma': row['call_weighted'],
                        'put_gamma': row['put_weighted'],
                        'net_gamma': row['call_weighted'] - row['put_weighted'],
                        'call_oi': row['call_oi'], 'put_oi':row['put_oi'],
                        'call_volume':row['call_volume'], 'put_volume':row['put_volume']})
    return {'expiry': expiry.isoformat(), 'contract_count': matched,
            'available_strikes': len(raw), 'selected_strikes': len(records),
            'band_pct': band_pct, 'band_lower': lower, 'band_upper': upper,
            'reference': reference, 'rows': records,
            'method': f'NDX: (OI+Volume) × Gamma; Call - Put; strike in ±{band_pct:g}% dal close precedente'}

"""Read-only adapter from Serafini SPX REPLAY to MT4 (no gamma recalculation).

Run from Serafini-SPX-Dashboard repo root, with this file placed there.
Requires existing Supabase Storage and es_spx_basis.download_and_calculate.
Output feed contains levels only; publish to the existing PRIVATE spx-snapshots bucket.
"""
import csv
import hashlib
import io
import json
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from storage import Store
from gamma_replay import _official_frames, _study_frames
from es_spx_basis import download_and_calculate

ROME = ZoneInfo('Europe/Rome')


def build_feed(store, day, basis_result):
    basis_raw = float(basis_result['basis'])
    if not math.isfinite(basis_raw):
        raise ValueError('Differenziale automatico ES-SPX non valido')
    # Stessa regola della Dashboard: arrotondamento all'intero con ROUND_HALF_UP.
    # Non spostare le barre MT4 di frazioni di punto (es. +40.307674 -> +40).
    basis = int(Decimal(str(basis_raw)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    iso = day.isocalendar()
    week = f'{iso.year}-W{iso.week:02d}'
    logs = store.list(f'{week}/logs/')
    frames = _study_frames(store, week, day)
    # Official frames take precedence for the same slot, exactly as in REPLAY.
    frames.update(_official_frames(store, week, day, logs))
    candidates = []
    for slot, frame in frames.items():
        clock = frame.get('clock', '')
        try:
            downloaded = datetime.fromisoformat(f'{day.isoformat()}T{clock}').replace(tzinfo=ROME)
        except ValueError:
            continue
        if downloaded <= datetime.now(ROME):
            candidates.append((downloaded, slot, frame))
    if not candidates:
        raise ValueError('Nessun frame REPLAY valido disponibile per oggi')
    _, slot, frame = max(candidates, key=lambda t: t[0])
    writer_io = io.StringIO(newline='')
    out = csv.writer(writer_io, lineterminator='\n')
    meta = [
        ('schema', 'serafini_mt4_v3'),
        ('date_rome', day.isoformat()),
        ('slot_rome', slot),
        ('download_clock_rome', frame['clock']),
        ('origin', frame['origin']),
        ('basis_es_spx', f'{basis:.6f}'),
        ('basis_date', str(basis_result.get('reference_date') or '')),
        ('basis_source', str(basis_result.get('source') or '')),
        ('published_utc', datetime.now(timezone.utc).isoformat()),
        ('cboe_timestamp', str(frame.get('cboe_timestamp_original') or '')),
    ]
    for item in meta:
        out.writerow(item)
    out.writerow(['profile', 'expiry', 'strike_spx', 'price_es', 'net_gamma'])
    expiry0 = day.isoformat()
    # Always use the same expiry selection as gamma_replay.py, not a new convention.
    from gamma_replay import weekly_expiry
    expiryw = weekly_expiry(day).isoformat()
    for kind, expiry in (('0DTE', expiry0), ('Weekly', expiryw)):
        rows = (frame.get('profiles') or {}).get(kind, {}).get('rows') or []
        if len(rows) != 50 or len({float(r['strike_spx']) for r in rows}) != 50:
            raise ValueError(f'{kind}: 50 strike univoci richiesti')
        for row in sorted(rows, key=lambda r: float(r['strike_spx'])):
            strike = float(row['strike_spx'])
            net = float(row['net_gamma'])
            if not all(map(math.isfinite, (strike, net, strike + basis))):
                raise ValueError('Valore non finito nel profilo')
            out.writerow([kind, expiry, f'{strike:.3f}', f'{strike+basis:.3f}', f'{net:.10f}'])
    return writer_io.getvalue().encode('utf-8')


def publish_levels(data, url, secret, bucket='spx-snapshots'):
    endpoint = f"{url.rstrip('/')}/storage/v1/object/{quote(bucket,safe='')}/mt4/latest.csv"
    headers = {'apikey': secret, 'Authorization': f'Bearer {secret}',
               'Content-Type': 'text/csv', 'x-upsert': 'true',
               'cache-control': 'no-store'}
    request = Request(endpoint, data=data, headers=headers, method='POST')
    with urlopen(request, timeout=45) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f'HTTP {response.status} durante pubblicazione')
    return hashlib.sha256(data).hexdigest()


def main():
    day = datetime.now(ROME).date()
    if day.weekday() >= 5:
        print('No weekday session; skipping MT4 export')
        return
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    store = Store(url, key, os.environ.get('SUPABASE_BUCKET', 'spx-snapshots'))
    # The exact existing automatic ES-SPX function, not a parallel conversion.
    basis_result = download_and_calculate(day)
    data = build_feed(store, day, basis_result)
    sha = publish_levels(data, url, key,
                         os.environ.get('SUPABASE_BUCKET', 'spx-snapshots'))
    print(f'MT4 feed published for {day}: SHA256={sha[:12]}')


if __name__ == '__main__':
    main()

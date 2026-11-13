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
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from storage import Store
from gamma_replay import _official_frames, _study_frames
from es_spx_basis import download_and_calculate

ROME = ZoneInfo('Europe/Rome')


def edge_levels_metadata(result):
    """Use existing Edge's SPX Wall/Flip. NEVER derive them from Serafini bars.

    Gamma Flip may legitimately be unavailable if the modeled curve does not
    cross zero. Independent missing levels remain N/D, never zero-price lines.
    """
    mapping = (
        ("call_wall", "gex_call_wall_spx"),
        ("put_wall", "gex_put_wall_spx"),
        ("flip", "gex_gamma_flip_spx"),
    )
    levels = {}
    for source, destination in mapping:
        value = result.get(source)
        try:
            price = float(value)
            if math.isfinite(price) and price > 0:
                levels[destination] = price
        except (ValueError, TypeError, OverflowError):
            pass
    return levels


def verified_edge_from_saved_replay(store, week, day, slot, frame):
    """Reuse the verified archived Edge sidecar for the SAME Replay download.

    The Replay raw gzip is normally not retained in Supabase. The archived
    Edge report was calculated at acquisition by the pinned independent
    engine. Use the dashboard's strict pair validator, never a nearby slot.
    """
    if frame.get("origin") != "STUDIO NON UFFICIALE":
        return None
    try:
        # Available on the publisher PYTHONPATH=dashboard-edge:edge-pinned.
        from edge_replay_data import read_pair
        compact, sidecar, report, clock = read_pair(store, week, day, slot)
        if (clock.strftime("%H:%M:%S") != frame.get("clock")
                or compact.get("cboe_timestamp_original")
                   != frame.get("cboe_timestamp_original")
                or sidecar.get("cboe_sha256") != report.get("sha256")
                or not math.isclose(float(report["spot_spx"]),
                                    float(frame["spot"]), abs_tol=0.01)
                or report.get("quality_sufficient") is not True):
            return None
        sign = report.get("modeled_gamma_sign")
        net = float(report.get("net_gex"))
        if (not math.isfinite(net)
                or ("Positive" if net > 0 else "Negative" if net < 0 else "Zero")
                   != sign):
            return None
        return {
            "gex_model": "EDGE_0DTE",
            "gex_modeled_sign": sign,
            "gex_cboe_timestamp": frame["cboe_timestamp_original"],
            "gex_expiry": day.isoformat(),
            "gex_source_sha256": report["sha256"],
            **edge_levels_metadata({
                "call_wall": report.get("call_wall_spx"),
                "put_wall": report.get("put_wall_spx"),
                "flip": report.get("gamma_flip_spx"),
            }),
        }
    except (RuntimeError, OSError, KeyError, TypeError, ValueError,
            OverflowError, ImportError, AttributeError):
        return None


def verified_edge_from_artifact(frame, day, artifact_dir=None, analyzer=None):
    """Optional exact-snapshot Edge 0DTE sign for MT4 v3.44 (never inferred).

    Prefer the already-downloaded ORIGINAL Cboe bytes from the selected
    OFFICIAL frame, otherwise the triggering Replay/Official artifact.
    No extra Cboe request or cross-slot/cross-day reuse. Invalid source,
    missing data or quality failure => None without blocking NET GAMMA.
    """
    base = artifact_dir or os.environ.get('MT4_CBOE_RAW_DIR', '')
    raw_from_frame = frame.get('_mt4_original_cboe_bytes')
    candidates = []
    if isinstance(raw_from_frame, bytes) and raw_from_frame:
        candidates.append(raw_from_frame)
    if base:
        candidates.extend(sorted(Path(base).glob('**/SPX_CBOE_*.json.gz')))
    if not candidates:
        return None
    expected_ts = str(frame.get('cboe_timestamp_original') or '')
    expected_spot = frame.get('spot')
    if not expected_ts or expected_spot is None:
        return None
    try:
        expected_spot = float(expected_spot)
        if not (math.isfinite(expected_spot) and expected_spot > 0):
            return None
    except (ValueError, TypeError):
        return None
    from gzip import decompress
    for original in candidates:
        try:
            raw = (original if isinstance(original, bytes)
                   else original.read_bytes())
            envelope = json.loads(decompress(raw))
            if envelope.get('timestamp') != expected_ts:
                continue
            spot = float(envelope['data']['current_price'])
            if not math.isfinite(spot) or abs(spot - expected_spot) > 0.0001:
                continue
            # Import only after raw timestamp + exact spot match.
            # Source is the pinned Edge engine already used by Serafini SPX.
            if analyzer is None:
                from edge_auto_engine import analyze
                analyzer = analyze
            result = analyzer(raw, '0DTE')
            sign = result.get('modeled_gamma_sign')
            net = float(result.get('net'))
            if result.get('symbol') != 'SPX' or result.get('mode') != '0DTE':
                continue
            if result.get('expiries') != [day.isoformat()]:
                continue
            if not result.get('quality_sufficient'):
                continue
            if not math.isfinite(net) or (
                'Positive' if net > 0 else 'Negative' if net < 0 else 'Zero'
            ) != sign:
                continue
            if sign not in ('Positive', 'Negative', 'Zero'):
                continue
            return {'gex_model': 'EDGE_0DTE',
                    'gex_modeled_sign': sign,
                    'gex_cboe_timestamp': expected_ts,
                    'gex_expiry': day.isoformat(),
                    'gex_source_sha256': hashlib.sha256(raw).hexdigest(),
                    **edge_levels_metadata(result)}
        except (OSError, EOFError, ValueError, TypeError, KeyError,
                RuntimeError, ImportError, MemoryError):
            # GEX is optional; do not interrupt the Serafini feed.
            continue
    return None


def build_feed(store, day, basis_result, mode="Replay", strike_count=50, edge_artifacts=None, edge_analyzer=None):
    """50 Serafini per feed legacy; 100 MT4 solo da estensione CBOE verificata."""
    if strike_count not in (50, 100):
        raise ValueError("Sono consentiti solo 50 o 100 strike per profilo")
    if mode not in ("Replay", "Ufficiale"):
        raise ValueError(f"Modalita feed MT4 sconosciuta: {mode}")
    basis_raw = float(basis_result['basis'])
    if not math.isfinite(basis_raw):
        raise ValueError('Differenziale automatico ES-SPX non valido')
    # Stessa regola della Dashboard: arrotondamento all'intero con ROUND_HALF_UP.
    # Non spostare le barre MT4 di frazioni di punto (es. +40.307674 -> +40).
    basis = int(Decimal(str(basis_raw)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    iso = day.isocalendar()
    week = f'{iso.year}-W{iso.week:02d}'
    logs = store.list(f'{week}/logs/')
    # The official original Cboe bytes have already been downloaded by
    # _official_frames to compute Serafini's 100 strikes. Reuse them only
    # for the optional 100-strike MT4 Edge sign; ordinary 50-strike
    # and Replay dashboard call sites remain unchanged.
    official = _official_frames(
        store, week, day, logs, include_mt4_raw=(strike_count == 100))
    if mode == "Ufficiale":
        # Solo i rilevamenti ufficiali come il Pine della Dashboard.
        frames = official
    else:
        # Comportamento preesistente: ultimo Replay, incluse le acquisizioni ufficiali.
        frames = _study_frames(store, week, day)
        # Official frames take precedence for the same slot, exactly as in REPLAY.
        frames.update(official)
    candidates = []
    for slot, frame in frames.items():
        if strike_count == 100:
            # I vecchi Replay contengono solo 50 strike: non allargarli
            # artificialmente ne' pubblicarli nel feed esteso.
            profiles = frame.get('profiles') or {}
            reference = float(frame.get('reference_spx') or 0)
            if reference <= 0:
                continue
            complete = True
            for kind in ("0DTE", "Weekly"):
                rows = (profiles.get(kind) or {}).get('mt4_rows') or []
                prices = [float(item['strike_spx']) for item in rows]
                if (len(prices) != 100 or len(set(prices)) != 100
                        or sum(price < reference for price in prices) != 50
                        or sum(price > reference for price in prices) != 50):
                    complete = False
                    break
            if not complete:
                continue
        clock = frame.get('clock', '')
        try:
            downloaded = datetime.fromisoformat(f'{day.isoformat()}T{clock}').replace(tzinfo=ROME)
        except ValueError:
            continue
        # Stessa esclusione del Replay online per 0DTE post-scadenza:
        # 16:00 New York, con DST effettivo; nessuna stima di prezzi.
        ny_download = downloaded.astimezone(ZoneInfo('America/New_York'))
        if ny_download.hour >= 16:
            continue
        if downloaded <= datetime.now(ROME):
            candidates.append((downloaded, slot, frame))
    if not candidates:
        raise ValueError(f'Nessun frame {mode} valido disponibile per oggi')
    _, slot, frame = max(candidates, key=lambda t: t[0])
    writer_io = io.StringIO(newline='')
    out = csv.writer(writer_io, lineterminator='\n')
    meta = [
        ('schema', 'serafini_mt4_v4' if strike_count == 100 else 'serafini_mt4_v3'),
        ('mode', mode),
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
    # Il CSV legacy 50 strike resta perfettamente compatibile: nessuna
    # nuova riga di metadati introdotta nella sua versione v3.
    if strike_count == 100:
        meta.insert(2, ('profile_strikes', '100'))
        # V4 only: append independent Edge modeled sign to the SAME
        # authenticated snapshot, never modify the legacy v3/50 feed.
        verified = verified_edge_from_artifact(
            frame, day, artifact_dir=edge_artifacts, analyzer=edge_analyzer)
        if verified is None:
            verified = verified_edge_from_saved_replay(store, week, day, slot, frame)
        if verified:
            # The existing Serafini basis is used for ALL three ES overlays.
            # No second basis download and no independent price estimation.
            for spx_key in (
                'gex_call_wall_spx', 'gex_put_wall_spx', 'gex_gamma_flip_spx'
            ):
                spx = verified.get(spx_key)
                if spx is not None and math.isfinite(float(spx)) and float(spx) > 0:
                    verified[spx_key.replace('_spx', '_es')] = float(spx) + basis
            meta.extend((key, verified[key]) for key in (
                'gex_model', 'gex_modeled_sign',
                'gex_cboe_timestamp', 'gex_expiry',
                'gex_source_sha256',
                'gex_call_wall_spx', 'gex_call_wall_es',
                'gex_put_wall_spx', 'gex_put_wall_es',
                'gex_gamma_flip_spx', 'gex_gamma_flip_es',
            ) if key in verified)
        print('MT4 100 Edge GEX ' + mode + ' ' + str(day) + ' ' + slot
              + ': ' + (verified['gex_modeled_sign'] if verified else 'N/D')
              + (' | Call/Put/Flip ' + str(sum(
                  key in verified for key in (
                      'gex_call_wall_es','gex_put_wall_es','gex_gamma_flip_es')))
                 + '/3' if verified else ' | raw/sidecar/quality not verified'))
    for item in meta:
        out.writerow(item)
    out.writerow(['profile', 'expiry', 'strike_spx', 'price_es', 'net_gamma'])
    expiry0 = day.isoformat()
    # Always use the same expiry selection as gamma_replay.py, not a new convention.
    from gamma_replay import weekly_expiry
    expiryw = weekly_expiry(day).isoformat()
    for kind, expiry in (('0DTE', expiry0), ('Weekly', expiryw)):
        selected_profile = (frame.get('profiles') or {}).get(kind, {})
        field = 'mt4_rows' if strike_count == 100 else 'rows'
        rows = selected_profile.get(field) or []
        if len(rows) != strike_count or len({float(r['strike_spx']) for r in rows}) != strike_count:
            raise ValueError(f'{kind}: {strike_count} strike univoci richiesti')
        for row in sorted(rows, key=lambda r: float(r['strike_spx'])):
            strike = float(row['strike_spx'])
            net = float(row['net_gamma'])
            if not all(map(math.isfinite, (strike, net, strike + basis))):
                raise ValueError('Valore non finito nel profilo')
            out.writerow([kind, expiry, f'{strike:.3f}', f'{strike+basis:.3f}', f'{net:.10f}'])
    return writer_io.getvalue().encode('utf-8')


def publish_levels(data, url, secret, bucket='spx-snapshots', target='mt4/latest.csv'):
    if target not in ('mt4/latest.csv', 'mt4/official.csv',
                      'mt4/latest100.csv', 'mt4/official100.csv'):
        raise ValueError('Destinazione MT4 non consentita')
    endpoint = f"{url.rstrip('/')}/storage/v1/object/{quote(bucket,safe='')}/{target}"
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
    bucket = os.environ.get('SUPABASE_BUCKET', 'spx-snapshots')
    # Replay resta il feed di default e conserva ESATTAMENTE l'URL precedente.
    data_replay = build_feed(store, day, basis_result, mode='Replay')
    sha_replay = publish_levels(data_replay, url, key, bucket,
                                target='mt4/latest.csv')
    print(f'MT4 Replay (DEFAULT) for {day}: SHA256={sha_replay[:12]}')
    # Secondo feed indipendente; la sua assenza non sostituisce il Replay.
    data_official = build_feed(store, day, basis_result, mode='Ufficiale')
    sha_official = publish_levels(data_official, url, key, bucket,
                                  target='mt4/official.csv')
    print(f'MT4 Ufficiale for {day}: SHA256={sha_official[:12]}')
    # V4 e' un feed SEPARATO: i vecchi indicatori MT4 v3 continuano
    # a leggere gli stessi file 50 strike senza alcuna incompatibilita'.
    for mode, target in (('Replay', 'mt4/latest100.csv'),
                         ('Ufficiale', 'mt4/official100.csv')):
        try:
            expanded = build_feed(store, day, basis_result, mode=mode, strike_count=100)
        except ValueError as exc:
            print(f'MT4 100 strike {mode}: nessun frame 50+50 disponibile ({exc}).')
            continue
        digest = publish_levels(expanded, url, key, bucket, target=target)
        print(f'MT4 100 strike {mode} for {day}: SHA256={digest[:12]}')


if __name__ == '__main__':
    main()

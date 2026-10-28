"""Esportazione verificabile NET GAMMA per analisi, senza segnali AI.

Solo lettura da Supabase; niente dati originali modificati e nessun calcolo
predittivo. PDF sintetico; JSON contiene 50 strike per ogni profilo valido.
"""
import gzip
import hashlib
import io
import json
import math
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import streamlit as st
from net_gamma import profile, read_snapshot
from ai_replay import attach_replay
from spx_market_clock import cash_open_rome, spxw_expiry_close_rome
from volume_audit import compare_volume_profiles, inspect_raw_expiry, raw_transition_diagnostic

ROME = ZoneInfo('Europe/Rome')


def _day_from_name(name):
    return date.fromisoformat(name[9:13] + '-' + name[13:15] + '-' + name[15:17]) if name.startswith('SPX_CBOE_') else None


def _weekly_expiry(day):
    from datetime import timedelta
    ahead = (4 - day.weekday()) % 7
    if ahead == 0:  # il venerdi si usa la scadenza del venerdi successivo
        ahead = 7
    return day + timedelta(days=ahead)


def _to_rome(stamp):
    try:
        dt = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        return dt.astimezone(ROME).strftime('%d/%m/%Y %H:%M:%S') if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _quality(snapshot):
    opts = snapshot['data']['options']
    def nonzero(field):
        return sum(1 for x in opts if _is_nonzero(x.get(field)))
    counts = {field: nonzero(field) for field in ('gamma', 'delta', 'volume', 'iv')}
    counts['contracts'] = len(opts)
    return ('VALIDO' if counts['gamma'] else 'NON VALIDO — Gamma assenti'), counts


def _is_nonzero(value):
    try:
        val = float(value)
        return math.isfinite(val) and val != 0
    except (ValueError, TypeError, OverflowError):
        return False


def _norm(v):
    return round(float(v), 6)


def _brief(rows):
    """Graduatoria matematica, non classificazione supporti/target."""
    return [{ 'strike_spx': r['strike_spx'], 'strike_es': r.get('strike_es'),
              'net_gamma': _norm(r['net_gamma'])}
            for r in sorted(rows, key=lambda r: abs(r['net_gamma']), reverse=True)[:8]]


def build_report(store, week, day, reference, offset=None, selected_levels=None):
    """Gli errori di un singolo snapshot non annullano il report della giornata."""
    date_key = day.strftime('%Y%m%d')
    objects = sorted((o['name'] for o in store.list(f'{week}/snapshots/')
                      if o['name'].startswith('SPX_CBOE_' + date_key + '_')))
    if not objects:
        raise ValueError('Nessuno snapshot per la data selezionata')
    raw_logs = {}
    for obj in store.list(f'{week}/logs/'):
        try:
            log = json.loads(store.download(f"{week}/logs/{obj['name']}"))
            raw_logs[log.get('file', '')] = log
        except (ValueError, KeyError, TypeError):
            continue
    weekly = _weekly_expiry(day)
    report = {
        'schema_version': 'serafini_spx_ai_report_v1',
        'report_date': day.isoformat(),
        'generated_at_rome': datetime.now(ROME).isoformat(timespec='seconds'),
        'sources': {'chains': 'Cboe SPX original JSON gzip', 'storage': 'Supabase privato',
                    'basis': 'dato verificato in dashboard o inserito manualmente',
                    'prices_after_snapshot': 'NON INCLUSI', 'volume_profile_1y': 'NON INCLUSO',
                    'stdev_350': 'NON INCLUSA'},
        'method': {'net_gamma_formula': '(OI_CALL+VOL_CALL)*GAMMA_CALL - (OI_PUT+VOL_PUT)*GAMMA_PUT',
                   'strike_selection': '25 strike SPX strettamente sotto e 25 strettamente sopra il riferimento',
                   'weekly_friday': 'venerdi successivo', 'signal': 'nessun segnale o target automatico'},
        'parameters': {'reference_spx_previous_close': float(reference),
                       'reference_status': 'Fornito o confermato dall’utente; verificare con cash close ufficiale',
                       'es_minus_spx': None if offset is None else float(offset),
                       'basis_is_usable': offset is not None},
        'expiries': {'0DTE': day.isoformat(), 'Weekly': weekly.isoformat()},
        'snapshots': [], 'pairwise_changes': [], 'warnings': [],
    }
    raw_probes = {}  # Analisi dei campi originali; non esporta la chain integrale.
    # Scarica soltanto gli snapshot del giorno selezionato; richieste di rete parallele limitate.
    # L'elaborazione rimane nell'ordine cronologico dei file.
    def fetch_snapshot(name):
        return store.download(f'{week}/snapshots/{name}')
    with ThreadPoolExecutor(max_workers=min(3, len(objects))) as pool:
        downloads = list(pool.map(fetch_snapshot, objects))
    for name, blob in zip(objects, downloads):
        path = f'{week}/snapshots/{name}'
        rec = {'file': path, 'status': 'DA VERIFICARE', 'profiles': {}}
        try:
            rec['sha256_original_gzip'] = hashlib.sha256(blob).hexdigest()
            snap = read_snapshot(blob)
            rec['cboe_timestamp_original'] = snap.get('timestamp')
            log = raw_logs.get(path, {})
            rec['download_rome'] = _to_rome(log.get('download_utc'))
            rec['download_utc'] = log.get('download_utc')
            rec['acquisition_slot'] = log.get('scheduled_slot')
            rec['acquisition_status'] = log.get('status')
            rec['acquisition_classification'] = (
                'STUDIO_STORICO_DA_ARCHIVIO'
                if rec['acquisition_slot'] == '30 13 * * 1-5' else
                'UFFICIALE' if rec['acquisition_slot'] in (
                    '30 7 * * 1-5', '50 15 * * 1-5', '30 20 * * 1-5')
                else 'NON CLASSIFICATO'
            )
            rec['cboe_spot_spx'] = snap['data'].get('current_price')
            rec['cboe_prev_close_spx'] = snap['data'].get('prev_day_close')
            rec['status'], rec['quality_counts'] = _quality(snap)
            if rec['status'] == 'VALIDO':
                for label, expiry in [('0DTE', day), ('Weekly', weekly)]:
                    try:
                        result = profile(snap, expiry, float(reference))
                        rows = []
                        for source in result['rows']:
                            row = dict(source)
                            row['strike_es'] = None if offset is None else _norm(row['strike_spx'] + float(offset))
                            rows.append(row)
                        rec['profiles'][label] = {'expiry': expiry.isoformat(), 'contracts': result['contracts'],
                                                   'selection': result['selection'], 'strike_count': len(rows),
                                                   'main_concentrations_by_absolute_net': _brief(rows), 'strikes': rows}
                        if rows:
                            probe = inspect_raw_expiry(
                                snap['data']['options'], expiry,
                                {float(v['strike_spx']) for v in rows})
                            raw_probes.setdefault(name, {})[label] = probe
                            rec.setdefault('raw_volume_field_quality', {})[label] = probe['counts']
                        else:
                            report['warnings'].append(f'{name} {label}: nessuna opzione per la scadenza')
                    except (ValueError, KeyError, TypeError) as err:
                        rec['profiles'][label] = {'error': str(err)[:220]}
                        report['warnings'].append(f'{name} {label}: profilo non disponibile')
            else:
                rec['excluded_from_net_gamma'] = True
        except (ValueError, TypeError, KeyError, OSError, EOFError) as err:
            rec['status'] = 'ILLEGIBILE'
            rec['error'] = type(err).__name__
            rec['excluded_from_net_gamma'] = True
        report['snapshots'].append(rec)
    ordered = [r for r in report['snapshots'] if r['status'] == 'VALIDO']
    ordered.sort(key=lambda r: (r.get('download_utc') or '', r['file']))
    for old, new in zip(ordered, ordered[1:]):
        comp = {'initial_file': old['file'], 'final_file': new['file'],
                'initial_download_rome': old.get('download_rome'), 'final_download_rome': new.get('download_rome'),
                'profiles': {}, 'volume_audit': {}}
        comp['raw_volume_diagnostic'] = {}
        for label in ('0DTE', 'Weekly'):
            p_old = old['profiles'].get(label, {})
            p_new = new['profiles'].get(label, {})
            a = p_old.get('strikes', [])
            b = p_new.get('strikes', [])
            old_expiry, new_expiry = p_old.get('expiry'), p_new.get('expiry')
            if old_expiry and new_expiry and old_expiry != new_expiry:
                comp['profiles'][label] = {
                    'status': 'SCADENZA DIVERSA', 'common_strikes': 0, 'changes': [],
                    'old_expiry': old_expiry, 'new_expiry': new_expiry}
                comp['volume_audit'][label] = compare_volume_profiles(
                    a, b, old_expiry=old_expiry, new_expiry=new_expiry)
                report['warnings'].append(
                    f"{label}: {old_expiry} -> {new_expiry}; confronto escluso.")
                continue
            raw_old = raw_probes.get(old['file'].rsplit('/', 1)[-1], {}).get(label)
            raw_new = raw_probes.get(new['file'].rsplit('/', 1)[-1], {}).get(label)
            if raw_old and raw_new:
                comp['raw_volume_diagnostic'][label] = raw_transition_diagnostic(
                    raw_old, raw_new, old.get('download_utc'), new.get('download_utc'))
            oldmap = {r['strike_spx']: r for r in a}
            newmap = {r['strike_spx']: r for r in b}
            common = sorted(oldmap.keys() & newmap.keys())
            changes = []
            for strike in common:
                prior = float(oldmap[strike]['net_gamma']); later = float(newmap[strike]['net_gamma'])
                changes.append({'strike_spx': strike,
                                'strike_es': None if offset is None else _norm(strike + offset),
                                'net_initial': _norm(prior), 'net_final': _norm(later),
                                'delta_net': _norm(later - prior),
                                'changed_sign': (prior < 0 < later or prior > 0 > later),
                                'sign_transition': ('PUT_TO_CALL' if prior < 0 < later else
                                                    'CALL_TO_PUT' if prior > 0 > later else None)})
            comp['profiles'][label] = {'common_strikes': len(common), 'changes': changes}
            audit = compare_volume_profiles(a, b, old.get('quality_counts'), new.get('quality_counts'), old_expiry, new_expiry)
            comp['volume_audit'][label] = audit
            if audit['alert']:
                report['warnings'].append(
                    f"ALLERTA VOLUME {label}: {old['file'].split('/')[-1]} -> "
                    f"{new['file'].split('/')[-1]}; "
                    f"volume {audit['volume_initial']:.0f} -> {audit['volume_final']:.0f} "
                    f"({audit['volume_change_pct']:+.1f}%), OI {audit['oi_change_pct']:+.1f}%. "
                    "NET GAMMA potrebbe non essere direttamente confrontabile."
                )
        report['pairwise_changes'].append(comp)
    volume_alerts = [
        {'initial_file': comp['initial_file'], 'final_file': comp['final_file'],
         'profile': label, 'audit': audit}
        for comp in report['pairwise_changes']
        for label, audit in comp['volume_audit'].items()
        if audit.get('alert')
    ]
    possible_resets = [
        {'initial_file': comp['initial_file'], 'final_file': comp['final_file'],
         'profile': label, 'diagnostic': diag}
        for comp in report['pairwise_changes']
        for label, diag in comp.get('raw_volume_diagnostic', {}).items()
        if diag.get('possible_new_session_volume_reset')
    ]
    report['volume_integrity'] = {
        'status': 'ALLERTA' if volume_alerts else 'NESSUNA ALLERTA DA SOGLIA',
        'alert_count': len(volume_alerts),
        'possible_us_open_reset_count': len(possible_resets),
        'possible_us_open_resets': possible_resets,
        'alerts': volume_alerts,
        'note': ('Audit sullo stesso contratto Cboe; distinguere campi mancanti '
                 'da valori zero. Un possibile reset all apertura USA e una '
                 'ipotesi da verificare, non una causa certificata. '
                 'Nessuna modifica a NET GAMMA.')
    }
    if offset is None:
        report['warnings'].append('Differenziale ES–SPX assente: strike_es=null; usare soltanto strike SPX.')
    report['warnings'].append('Le concentrazioni e le variazioni sono descrittive, non dimostrano acquisti/vendite reali di opzioni.')
    # Contesto indipendente: non modifica la formula NET GAMMA.
    report['schema_version'] = 'serafini_spx_ai_report_v9'
    report['market_context'] = {'vix_curve': {'status': 'NON DISPONIBILE'},
                                'asia_session': {'status': 'NON DISPONIBILE'}}
    # Identico motore di volatilita del quadro operativo: VX + range Daily ES.
    # Non modificare profili, formule o selezione strike Cboe.
    try:
        from vix_regime import volatility_context
        volatility = volatility_context(day)
        report['market_context'].update(volatility)
        if volatility['breakout_buffer']['buffer_es_points'] is None:
            report['warnings'].append(
                'Buffer standard non determinabile: verificare entrambi '
                'i fattori VX e Range Daily ES della seduta precedente.')
        if volatility['previous_daily_range'].get('status') != 'VALIDO':
            report['warnings'].append(
                'Range Daily ES precedente non disponibile: non presumere bassa volatilita.')
        if volatility['vix_curve'].get('status') != 'VALIDO':
            report['warnings'].append(
                'Curva VX precedente non disponibile: non presumere contango.')
    except Exception as exc:
        report['market_context']['breakout_buffer'] = {
            'status': 'NON DETERMINABILE', 'buffer_es_points': None,
            'reason': type(exc).__name__}
        report['warnings'].append(
            'Contesto volatilita incompleto: impossibile determinare il buffer.')
    # Riutilizza il modulo 5m già usato dal quadro operativo, non il vecchio range 15m.
    try:
        from asia_gamma import asian_session, level_events, asia_range_reading
        asia = asian_session(day)
        result = {**asia, 'status': 'VALIDO', 'date': day.isoformat(),
                  'session': '00:00–09:00 Europe/Rome',
                  'close_0900_es': asia['close'],
                  'range_reading_0900': asia_range_reading(asia),
                  'relative_to_c1_p1': 'NON DETERMINABILE'}
        levels = selected_levels or {}
        # Anti-look-ahead: un livello definito dopo le 09:00 non puo'
        # essere presentato come un verdetto conoscibile alle 09:00.
        source_name = str(levels.get('snapshot') or '')
        source_rec = next((item for item in report['snapshots']
                           if item['file'].rsplit('/', 1)[-1] == source_name), None)
        source_time = None
        if source_rec and source_rec.get('download_utc'):
            try:
                source_time = datetime.fromisoformat(
                    str(source_rec['download_utc']).replace('Z', '+00:00'))
                if source_time.tzinfo is None:
                    source_time = None
                else:
                    source_time = source_time.astimezone(ROME)
            except (TypeError, ValueError, OverflowError):
                source_time = None
        cutoff = datetime.combine(day, time(9, 0), tzinfo=ROME)
        levels_known_at_0900 = bool(
            source_time and source_time.date() == day and source_time <= cutoff)
        if source_name and not levels_known_at_0900:
            result['level_source_snapshot'] = source_name
            result['level_selection_timing'] = 'SUCCESSIVO ALLE 09:00 O NON VERIFICATO'
            result['context_reading_0900'] = (
                'Livelli C1/P1 selezionati da uno snapshot successivo alle 09:00 '
                'o senza orario attendibile: confronto ex post, non verdetto delle 09:00.')
            report['warnings'].append(
                'Anti-look-ahead Asia: livelli C1/P1 esclusi dal verdetto delle 09:00 '
                'perche lo snapshot selezionato non risulta acquisito entro le 09:00 Roma.')
        if levels_known_at_0900 and levels.get('basis') is not None and offset is not None and abs(float(levels['basis'])-float(offset)) < 1e-8 and abs(float(levels.get('reference_spx', -1))-float(reference)) < 1e-8:
            c1, p1 = levels.get('c1_spx'), levels.get('p1_spx')
            if c1 is not None and p1 is not None:
                c1_es, p1_es = float(c1)+offset, float(p1)+offset
                if p1_es < c1_es:
                    close = float(asia['close'])
                    if close < p1_es:
                        verdict, zone, color = 'CHIUSURA ASIATICA SOTTO P1 — ZONA INFERIORE', 'INFERIORE', 'ROSSO'
                    elif close > c1_es:
                        verdict, zone, color = 'CHIUSURA ASIATICA SOPRA C1 — ZONA SUPERIORE', 'SUPERIORE', 'VERDE'
                    else:
                        verdict, zone, color = 'CHIUSURA ASIATICA FRA P1 E C1 — FASCIA INTERMEDIA / FLIP NON VERIFICATO', 'INTERMEDIA', 'AMBRA'
                    reading = None
                    if zone == 'INTERMEDIA':
                        if float(asia['low']) < p1_es and float(asia['high']) < c1_es:
                            reading = ('Lettura contestuale incerta, con elementi di debolezza: '
                                       'minimo disponibile sotto P1, massimo disponibile sotto C1, '
                                       'chiusura sopra P1. Non e un segnale short.')
                        else:
                            reading = ('Fra P1 e C1: fascia intermedia; '
                                       'non identifica il vero Gamma Flip.')
                    result.update({'c1_es': c1_es, 'p1_es': p1_es,
                                   'c1_spx': float(c1), 'p1_spx': float(p1),
                                   'level_source_snapshot': levels.get('snapshot'),
                                   'level_selection': levels.get('selection'),
                                   'relative_to_c1_p1': zone, 'verdict_0900': verdict,
                                   'display_color': color, 'gamma_flip_calculated': False,
                                   'context_reading_0900': reading,
                                   'p1_crossings': level_events(asia, p1_es, 'P1')[0],
                                   'c1_crossings': level_events(asia, c1_es, 'C1')[0]})
        report['market_context']['asia_session'] = result
        if result['relative_to_c1_p1'] == 'NON DETERMINABILE' and not (
                source_name and not levels_known_at_0900):
            report['warnings'].append('Chiusura asiatica disponibile, ma C1/P1 selezionati, snapshot antecedente alle 09:00 e basis coerente non disponibili.')
    except Exception as exc:
        report['market_context']['asia_session']['reason'] = type(exc).__name__
        report['warnings'].append('Sessione asiatica 5m non disponibile o incompleta.')
    report['operational_verdict'] = {
        'direction': 'NON DETERMINATO',
        'status': 'DA CONFERMARE CON PREZZO ES',
        'asia_verdict_0900': report['market_context']['asia_session'].get('verdict_0900'),
        'note': 'La zona asiatica è contesto, non segnale LONG/SHORT. NET GAMMA e VX non bastano per un ingresso.'}
    report['sources']['vix_curve'] = 'Cboe VX mensili: settlement seduta precedente, se disponibile'
    report['sources']['previous_daily_range'] = (
        'Yahoo Finance ES=F, candela Daily della seduta precedente; '
        'range=(High-Low)/Close * 100, non necessariamente identico a ES1! TradingView')
    report['sources']['asia_session'] = (
        report['market_context']['asia_session'].get('source')
        or 'Yahoo ES=F 5m; recupero da Yahoo 1m soltanto se verificabile')
    report['sources']['replay'] = (
        'Profili compatti Cboe di studio intraday; archivio Replay separato '
        'dagli snapshot originali ufficiali')
    # Conversione reale dei fusi: l'ora legale USA ed europea cambia in
    # domeniche diverse. NON assumere 15:30 e 22:00 Roma per tutto l'anno.
    report['us_session_clock'] = {
        'timezone': 'America/New_York',
        'rome_timezone': 'Europe/Rome',
        'us_cash_open_rome': cash_open_rome(day).isoformat(),
        'spxw_0dte_expiry_close_rome': spxw_expiry_close_rome(day).isoformat(),
        'spxw_regular_expiry_close_new_york': '16:00',
        'note': ('Regola per seduta ordinaria SPXW. '
                 'Giornate con chiusura anticipata richiedono calendario Cboe.')
    }
    try:
        report['intraday_replay'] = attach_replay(
            store, report, week, day, float(reference), offset)
        rejected = report['intraday_replay'].get('rejected_files', [])
        if rejected:
            report['warnings'].append(
                f"Replay: {len(rejected)} file scartati per qualita o formato. "
                "Dettagli in intraday_replay.rejected_files.")
        if not report['intraday_replay']['timeline']:
            report['warnings'].append(
                'Nessun rilevamento Replay/ufficiale associato a uno slot '
                'cronologico disponibile per la giornata selezionata.')
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        # Un archivio Replay assente o temporaneamente inaccessibile non
        # interrompe l'analisi degli snapshot originali.
        report['intraday_replay'] = {
            'status': 'NON DISPONIBILE', 'reason': type(exc).__name__,
            'frames': [], 'timeline': [], 'transitions': [],
            'missing_slots': [], 'study_files_total': 0
        }
        report['warnings'].append(
            'Replay non accessibile; gli snapshot ufficiali restano nel report.')
    return report


def _acquisition_label(rec):
    """Etichetta leggibile per PDF; conserva il cron originale nel JSON.

    Gli orari dell'archivio sono quelli effettivi di download (Europe/Rome).
    """
    raw = str(rec.get('acquisition_slot') or '').strip()
    fixed = {
        '30 7 * * 1-5': 'Mattina 07:30',
        '50 15 * * 1-5': 'Pomeriggio 15:50',
        '30 20 * * 1-5': 'Sera 20:30',
        '30 13 * * 1-5': '13:30 (studio storico)',
        '46 15 * * 1-5': 'Pomeriggio 15:46',
        '07:30': 'Mattina 07:30',
        '15:46': 'Pomeriggio 15:46',
    }
    if raw in fixed:
        return fixed[raw]
    if raw.lower() in ('', 'manuale', 'test manuale', 'manual'):
        stamp = str(rec.get('download_rome') or '')
        if len(stamp) >= 16:
            return 'Manuale ' + stamp[11:16]
        return 'Manuale'
    return raw  # Non inventare la classificazione di eventuali slot futuri.


def pdf_summary(report):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=17*mm, rightMargin=17*mm,
                            topMargin=16*mm, bottomMargin=16*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SPXTitle', fontName='Helvetica-Bold', fontSize=16, leading=19, spaceAfter=8))
    styles.add(ParagraphStyle(name='SPXHeading', fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=10, spaceAfter=5))
    styles.add(ParagraphStyle(name='SPXSmall', fontName='Helvetica', fontSize=8, leading=11, spaceAfter=4))
    story = [Paragraph('Serafini SPX | Report giornaliero per analisi AI', styles['SPXTitle']),
             Paragraph(f"Data: {report['report_date']} | Snapshot: {len(report['snapshots'])} | "
                       f"Riferimento SPX: {report['parameters']['reference_spx_previous_close']:.4f} | "
                       f"ES-SPX: {report['parameters']['es_minus_spx'] if report['parameters']['es_minus_spx'] is not None else 'non disponibile'}", styles['SPXSmall']),
             Paragraph('Documento descrittivo. Nessuna previsione, validazione delle strutture o interpretazione di trade. I 50 strike di ciascun profilo sono nel JSON.', styles['SPXSmall'])]
    def table(cols, rows, widths=None):
        t = Table([cols]+rows, colWidths=widths, hAlign='LEFT', repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1f2937')),
                               ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                               ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                               ('FONTSIZE',(0,0),(-1,-1),7.3),('LEADING',(0,0),(-1,-1),9),
                               ('BOTTOMPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),
                               ('LINEBELOW',(0,1),(-1,-1),0.35,colors.HexColor('#d1d5db'))]))
        return t
    ctx = report.get('market_context', {})
    vx = ctx.get('vix_curve', {})
    asia = ctx.get('asia_session', {})
    story.append(Paragraph('Contesto di mercato', styles['SPXHeading']))
    if vx.get('status') == 'VALIDO':
        story.append(Paragraph(
            f"VX mensili ({vx['day']}): M1 {vx['m1']:.2f}, M2 {vx['m2']:.2f}; "
            f"{vx['regime']} {vx['contango_pct']:+.2f}%.", styles['SPXSmall']))
    else:
        story.append(Paragraph('Regime VX: NON DISPONIBILE.', styles['SPXSmall']))
    previous_daily = ctx.get('previous_daily_range') or {}
    if previous_daily.get('status') == 'VALIDO':
        story.append(Paragraph(
            f"Daily ES=F precedente ({previous_daily['day']}): "
            f"High {previous_daily['high']:.2f}; Low {previous_daily['low']:.2f}; "
            f"Close {previous_daily['close']:.2f}; "
            f"Range {previous_daily['range_points']:.2f} punti "
            f"({previous_daily['range_pct']:.2f}%). "
            "Fonte Yahoo, sessione Daily potenzialmente diversa da ES1!.", styles['SPXSmall']))
    else:
        story.append(Paragraph('Daily ES precedente: NON DISPONIBILE.', styles['SPXSmall']))
    buffer = ctx.get('breakout_buffer') or {}
    if buffer.get('buffer_es_points') is not None:
        story.append(Paragraph(
            f"<b>Buffer standard combinato: {buffer['buffer_es_points']} punti ES.</b> "
            f"Motivazione: {buffer.get('reason', 'n.d.')}. "
            "Non costituisce un segnale automatico.", styles['SPXSmall']))
    else:
        story.append(Paragraph(
            'Buffer standard: NON DETERMINABILE, dati Daily/VX incompleti.',
            styles['SPXSmall']))
    if asia.get('status') == 'VALIDO':
        story.append(Paragraph(
            f"Sessione asiatica ES 00:00-09:00 Roma: minimo {asia['low']:.2f}, "
            f"massimo {asia['high']:.2f}; chiusura 09:00 {asia['close_0900_es']:.2f}; "
            f"barre {asia['bars']}/{asia['expected']}.", styles['SPXSmall']))
        base = asia.get('range_reading_0900') or {}
        if base.get('verdict'):
            story.append(Paragraph(
                '<b>Responso Asia 09:00:</b> '
                + base['verdict'] + '. '
                + f"Chiusura al {base['close_position_pct']:.1f}% del range; "
                + f"{base['distance_from_low']:.2f} punti sopra il minimo. "
                + ('Estremi PROVVISORI per barre mancanti. '
                   if base.get('extremes_provisional') else '')
                + 'Lettura descrittiva, non segnale operativo.',
                styles['SPXSmall']))
        story.append(Paragraph(
            'Confronto C1/P1: ' +
            asia.get('verdict_0900', 'NON DETERMINABILE (livelli e basis verificati non disponibili).'),
            styles['SPXSmall']))
        if asia.get('context_reading_0900'):
            story.append(Paragraph(asia['context_reading_0900'], styles['SPXSmall']))
        if asia.get('missing_times'):
            story.append(Paragraph('Massimo/minimo Asia provvisori: barre mancanti ' +
                                   ', '.join(asia['missing_times']) + '.', styles['SPXSmall']))
        if asia.get('yahoo_1m_recovered_times'):
            story.append(Paragraph(
                'Barre ES=F 5m recuperate da Yahoo 1m agli orari '
                + ', '.join(asia['yahoo_1m_recovered_times'])
                + ', solo dopo conferma delle cinque barre da 1 minuto '
                'e confronto con candele 5m comuni.',
                styles['SPXSmall']))
    else:
        story.append(Paragraph('Sessione asiatica ES: NON DISPONIBILE.', styles['SPXSmall']))
    story.append(Paragraph('Verdetto direzionale: NON DETERMINATO. '
                           'La zona asiatica non costituisce un segnale di ingresso.', styles['SPXSmall']))
    story.append(Paragraph('Archivio e qualita dati', styles['SPXHeading']))
    entries=[]
    for rec in report['snapshots']:
        entries.append([rec.get('download_rome') or 'n.d.', rec.get('status',''),
                        _acquisition_label(rec)[:22],
                        rec['file'].split('/')[-1].replace('SPX_CBOE_','')[:24]])
    story.append(table(['Download Roma','Qualita','Slot','File Cboe'],entries,[39*mm,36*mm,38*mm,61*mm]))
    audits = [(comp, label, check)
              for comp in report.get('pairwise_changes', [])
              for label, check in comp.get('volume_audit', {}).items()
              if check.get('alert')]
    if audits:
        story.append(Paragraph('ATTENZIONE: comparabilita del Volume tra snapshot', styles['SPXHeading']))
        for comp, label, check in audits:
            story.append(Paragraph(
                f"<b>{label}</b>: {comp.get('initial_download_rome') or 'n.d.'} &gt; "
                f"{comp.get('final_download_rome') or 'n.d.'}; "
                f"Volume sugli strike comuni "
                f"{check['volume_initial']:.0f} &gt; {check['volume_final']:.0f} "
                f"({check['volume_change_pct']:+.1f}%), OI ({check['oi_change_pct']:+.1f}%). "
                "Possibile disomogeneita dei volumi: "
                "non attribuire automaticamente il cambiamento NET GAMMA alle posizioni.",
                styles['SPXSmall']))
    resets = report.get('volume_integrity', {}).get('possible_us_open_resets', [])
    if resets:
        story.append(Paragraph('Possibile reset giornaliero Volume alla riapertura USA', styles['SPXHeading']))
        for item in resets:
            check = item['diagnostic']
            story.append(Paragraph(
                f"<b>{item['profile']}</b>: {check['common_contracts']} contratti comuni, "
                f"Volume {check['volume_previous']:.0f} &gt; {check['volume_current']:.0f} "
                f"({check['volume_change_pct']:+.1f}%), "
                f"contratti con calo {check['contracts_volume_lower']}, "
                f"diventati zero {check['contracts_positive_to_zero']}, "
                f"diventati mancanti {check['contracts_become_missing']}. "
                "Ipotesi coerente con l'azzeramento di inizio seduta, non confermata.",
                styles['SPXSmall']))
    replay = report.get('intraday_replay', {})
    timeline = replay.get('timeline', [])
    story.append(Paragraph('Evoluzione intraday: ufficiali + Replay', styles['SPXHeading']))
    hours = report.get('us_session_clock') or {}
    if hours:
        story.append(Paragraph(
            'Apertura USA (09:30 NY): '
            + hours['us_cash_open_rome'][11:16] + ' Roma | '
            'Scadenza negoziazione SPXW 0DTE (16:00 NY): '
            + hours['spxw_0dte_expiry_close_rome'][11:16]
            + ' Roma. Orari con cambio DST automatico.',
            styles['SPXSmall']))
    story.append(Paragraph(
        f"{len(timeline)} rilevamenti nella cronologia; "
        f"{replay.get('official_count_in_timeline', 0)} ufficiali e "
        f"{replay.get('study_count_in_timeline', 0)} di studio. "
        "I Replay sono separati dagli snapshot ufficiali; "
        "50 strike per profilo nel JSON.", styles['SPXSmall']))
    if timeline:
        rows = []
        for item in timeline:
            one = item.get('profiles', {}).get('0DTE', {})
            two = item.get('profiles', {}).get('Weekly', {})
            source = ('Ufficiale' if item.get('source') == 'UFFICIALE'
                      else 'Replay')
            state = ('Cboe ripetuto' if item.get('cboe_timestamp_repeated')
                     else ('Ref diverso' if not item.get('reference_matches_report')
                           else '-'))
            is_expired = item.get('market_timing', {}).get('post_expiry', False)
            if is_expired:
                state = '0DTE POST-SCAD.'
            rows.append([item['slot_rome'], source,
                         ('POST' if is_expired else
                          (f"{one['net_total']:+.2f}" if 'net_total' in one else 'n.d.')),
                         f"{two['net_total']:+.2f}" if 'net_total' in two else 'n.d.',
                         state])
        story.append(table(['Roma', 'Fonte', 'NET 0DTE', 'NET Weekly', 'Nota'],
                           rows, [20*mm, 35*mm, 34*mm, 34*mm, 51*mm]))
        post_slots = replay.get('post_expiry_0dte_slots', [])
        if post_slots:
            story.append(Paragraph(
                '0DTE dopo scadenza: ' + ', '.join(post_slots) + '. '
                'Non utilizzato per confronto operativo o scala Replay/GIF. '
                'Valori originali conservati nel JSON; Weekly mantenuto.',
                styles['SPXSmall']))
        missing = replay.get('missing_slots', [])
        if missing:
            story.append(Paragraph(
                'Slot senza dati: ' + ', '.join(missing) + '.', styles['SPXSmall']))
        unavailable_vol = any(
            not p.get('volume_oi_available', False)
            for item in timeline for p in item.get('profiles', {}).values())
        if unavailable_vol:
            story.append(Paragraph(
                'Alcuni file Replay storici non contengono OI/Volume: '
                'campi null, mai sostituiti con zero.', styles['SPXSmall']))
        repeated = [r['slot_rome'] for r in timeline
                    if r.get('cboe_timestamp_repeated')]
        if repeated:
            story.append(Paragraph(
                'Timestamp Cboe invariato negli slot: ' + ', '.join(repeated)
                + '.', styles['SPXSmall']))
        story.append(Paragraph(
            'I saldi sono descrittivi sui 50 strike; non rappresentano il '
            'dealer GEX. Nessuna attribuzione causale delle variazioni Volume. '
            'Gli slot sovrapposti usano il rilevamento ufficiale.',
            styles['SPXSmall']))
    else:
        story.append(Paragraph('Replay non disponibile per la data selezionata.',
                               styles['SPXSmall']))
    story.append(Paragraph('Concentrazioni principali per snapshot (magnitudine NET)', styles['SPXHeading']))
    for rec in report['snapshots']:
        if rec['status'] != 'VALIDO':
            continue
        header = f"{rec.get('download_rome') or rec['file'].split('/')[-1]} | {_acquisition_label(rec)}"
        sub=[Paragraph(header,styles['SPXSmall'])]
        for label in ('0DTE','Weekly'):
            p=rec['profiles'].get(label, {})
            if 'error' in p:
                sub.append(Paragraph(f'{label}: non disponibile ({p["error"][:70]})',styles['SPXSmall']))
                continue
            main=p.get('main_concentrations_by_absolute_net', [])[:5]
            cells=', '.join(f"{r['strike_spx']:g}: {r['net_gamma']:+.2f}" for r in main)
            sub.append(Paragraph(f'<b>{label}:</b> {cells or "nessun livello"}',styles['SPXSmall']))
        story.extend(sub)
    story.append(Paragraph('Transizioni tra snapshot validi consecutivi', styles['SPXHeading']))
    for comp in report['pairwise_changes']:
        start=comp.get('initial_download_rome') or '?'; end=comp.get('final_download_rome') or '?'
        story.append(Paragraph(f'{start} &gt; {end}', styles['SPXSmall']))
        for label,p in comp['profiles'].items():
            transitions=[x for x in p['changes'] if x['changed_sign']]
            message=', '.join(f"{x['strike_spx']:g} ({x['sign_transition']})" for x in transitions[:12])
            if len(transitions)>12: message+=f' ... +{len(transitions)-12}'
            story.append(Paragraph(f'<b>{label}:</b> {p["common_strikes"]} strike comuni; {len(transitions)} cambi di segno. {message}',styles['SPXSmall']))
    story.append(Spacer(1,5*mm))
    story.append(Paragraph('Limiti: nessun prezzo successivo ES, Volume Profile, deviazione standard o esito dei trade. La comparabilita dei volumi non e garantita. Controllare lo snapshot Cboe e il riferimento SPX prima di ogni analisi causale. Gli strike netti non indicano il lato effettivo delle operazioni.', styles['SPXSmall']))
    doc.build(story)
    return buf.getvalue()


def export_zip(report):
    json_data=json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    pdf_data=pdf_summary(report)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        day=report['report_date']
        z.writestr(f'SPX_AI_{day}.json',json_data)
        z.writestr(f'SPX_Report_{day}.pdf',pdf_data)
        z.writestr('LEGGIMI.txt', 'Report derivato dagli snapshot Cboe archiviati in Supabase.\n'
                   'Il JSON contiene 50 strike per profilo di ogni acquisizione completa o Replay disponibile.\n'
                   'Ufficiali e Replay di studio restano separati; cronologia senza slot doppi.\n'
                   'Le chain Cboe originali complete non sono incluse; scarica lo ZIP settimanale.\n'
                   'Valori senza interpretazione dei flussi: NET GAMMA non prova acquisti o vendite reali.\n')
    return out.getvalue()


def render(store, week, snaps, logs):
    if not snaps:
        st.info('Nessuno snapshot disponibile per il report.')
        return
    dates=sorted({_day_from_name(o['name']) for o in snaps if _day_from_name(o['name'])},reverse=True)
    if not dates:
        st.info('Nessuna data riconoscibile nei nomi degli snapshot.')
        return
    day=st.selectbox('Data del report', dates, format_func=lambda d:d.strftime('%d/%m/%Y'),key=f'ai_date_{week}')
    first=next((o['name'] for o in snaps if o['name'].startswith('SPX_CBOE_'+day.strftime('%Y%m%d')+'_')),None)
    default_prev=0.0
    if first:
        preview_key = f'ai_prev_close_{week}_{first}'
        if preview_key not in st.session_state:
            try:
                s = read_snapshot(store.download(f'{week}/snapshots/{first}'))
                st.session_state[preview_key] = float(s['data'].get('prev_day_close') or 0)
            except (ValueError, KeyError, TypeError, EOFError, OSError):
                st.session_state[preview_key] = 0.0
        default_prev = st.session_state[preview_key]
    col1,col2=st.columns(2)
    with col1:
        ref=st.number_input('Chiusura SPX precedente (verificare)',min_value=0.01,
                            value=default_prev if default_prev>0 else 1.0,format='%.4f',key=f'ai_ref_{day}')
    basis_key=f'basis_override_{day.isoformat()}'
    saved_basis=str(st.session_state.get(basis_key,'')).strip()
    with col2:
        offset_txt=st.text_input('Differenziale ES–SPX (facoltativo)',value=saved_basis,
                                 key=f'ai_offset_{day}',help='Se non verificato, lascia vuoto: nel report verranno usati soltanto strike SPX.')
    try:
        offset=float(offset_txt.replace(',','.')) if offset_txt.strip() else None
        if offset is not None and not math.isfinite(offset):
            raise ValueError('Numero non finito')
    except ValueError:
        st.error('Differenziale non numerico.');return
    levels = st.session_state.get(f'asia_levels_{day.isoformat()}')
    if levels and (offset is None or levels.get('basis') is None or
                   abs(float(levels['basis'])-offset)>1e-8 or
                   abs(float(levels.get('reference_spx',-1))-float(ref))>1e-8):
        st.warning('C1/P1 selezionati nel quadro NET GAMMA non coerenti con riferimento/basis del report: confronto non disponibile.')
    elif not levels:
        st.info('Per includere C1/P1, apri NET GAMMA 0DTE / Weekly e seleziona i livelli per questa data.')
    st.caption('Include le catene originali archiviate e tutti i rilevamenti Replay '
               'disponibili per questa data. JSON: 50 strike 0DTE e Weekly '
               'per ciascun frame; PDF: sintesi cronologica. '
               'I Replay di studio restano separati dagli originali Cboe.')
    # Firma del formato: impedisce il download del vecchio ZIP v2 salvato
    # nella sessione dopo l'aggiornamento di codice, a parita di parametri.
    report_key = (week, day.isoformat(), ref, offset,
                  json.dumps(levels, sort_keys=True), 'serafini_spx_ai_report_v9_official_2030')
    if st.button('Prepara report per analisi AI',key=f'ai_build_{week}',type='primary'):
        try:
            with st.spinner('Lettura snapshot Cboe e preparazione report...'):
                report=build_report(store,week,day,ref,offset,selected_levels=levels)
                binary=export_zip(report)
            st.session_state['ai_report_ready']={'key':report_key,
                                                  'data':binary,'valid':sum(x['status']=='VALIDO' for x in report['snapshots']),
                                                  'total':len(report['snapshots']),
                                                  'timeline':len(report.get('intraday_replay', {}).get('timeline', [])),
                                                  'study':report.get('intraday_replay', {}).get('study_files_total', 0)}
        except Exception as exc:
            st.error(f'Impossibile preparare il report: {type(exc).__name__}: {str(exc)[:180]}')
    ready=st.session_state.get('ai_report_ready')
    if ready and ready['key']==report_key:
        st.success(f"Report pronto: {ready['valid']} snapshot completi validi "
                   f"su {ready['total']} · {ready['timeline']} rilevamenti "
                   f"nella cronologia · {ready['study']} Replay di studio.")
        st.download_button('Scarica report JSON + PDF (ZIP)',data=ready['data'],
                           file_name=f'Serafini_SPX_AI_{day.isoformat()}.zip',mime='application/zip',
                           key=f'ai_download_{day}')

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
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st
from net_gamma import profile, read_snapshot

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


def build_report(store, week, day, reference, offset=None):
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
    for name in objects:
        path = f'{week}/snapshots/{name}'
        rec = {'file': path, 'status': 'DA VERIFICARE', 'profiles': {}}
        try:
            blob = store.download(path)
            rec['sha256_original_gzip'] = hashlib.sha256(blob).hexdigest()
            snap = read_snapshot(blob)
            rec['cboe_timestamp_original'] = snap.get('timestamp')
            log = raw_logs.get(path, {})
            rec['download_rome'] = _to_rome(log.get('download_utc'))
            rec['download_utc'] = log.get('download_utc')
            rec['acquisition_slot'] = log.get('scheduled_slot')
            rec['acquisition_status'] = log.get('status')
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
                        if not rows:
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
                'profiles': {}}
        for label in ('0DTE', 'Weekly'):
            a = old['profiles'].get(label, {}).get('strikes', [])
            b = new['profiles'].get(label, {}).get('strikes', [])
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
        report['pairwise_changes'].append(comp)
    if offset is None:
        report['warnings'].append('Differenziale ES–SPX assente: strike_es=null; usare soltanto strike SPX.')
    report['warnings'].append('Le concentrazioni e le variazioni sono descrittive, non dimostrano acquisti/vendite reali di opzioni.')
    return report


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
    story.append(Paragraph('Archivio e qualita dati', styles['SPXHeading']))
    entries=[]
    for rec in report['snapshots']:
        entries.append([rec.get('download_rome') or 'n.d.', rec.get('status',''),
                        (rec.get('acquisition_slot') or 'n.d.')[:22],
                        rec['file'].split('/')[-1].replace('SPX_CBOE_','')[:24]])
    story.append(table(['Download Roma','Qualita','Slot','File Cboe'],entries,[39*mm,36*mm,38*mm,61*mm]))
    story.append(Paragraph('Concentrazioni principali per snapshot (magnitudine NET)', styles['SPXHeading']))
    for rec in report['snapshots']:
        if rec['status'] != 'VALIDO':
            continue
        header = f"{rec.get('download_rome') or rec['file'].split('/')[-1]} | {rec.get('acquisition_slot') or 'manuale'}"
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
    story.append(Paragraph('Limiti: nessun prezzo successivo ES, Volume Profile, deviazione standard o esito dei trade. Controllare lo snapshot Cboe e il riferimento SPX prima di ogni analisi causale. Gli strike netti non indicano il lato effettivo delle operazioni.', styles['SPXSmall']))
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
                   'Il JSON contiene 50 strike per ogni profilo disponibile di ogni snapshot valido.\n'
                   'Il PDF e sintetico. I file originali Cboe non sono inclusi; scarica separatamente lo ZIP settimanale.\n'
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
        try:
            s=read_snapshot(store.download(f'{week}/snapshots/{first}'))
            default_prev=float(s['data'].get('prev_day_close') or 0)
        except (ValueError,KeyError,TypeError,EOFError,OSError):
            pass
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
    st.caption('Include tutti gli snapshot della giornata; quelli senza Gamma sono segnalati e non elaborati. '
               'I 50 strike 0DTE e Weekly sono nel JSON. Il PDF e una sintesi, non contiene analisi AI.')
    if st.button('Prepara report per analisi AI',key=f'ai_build_{week}',type='primary'):
        try:
            with st.spinner('Lettura snapshot Cboe e preparazione report...'):
                report=build_report(store,week,day,ref,offset)
                binary=export_zip(report)
            st.session_state['ai_report_ready']={'key':(week,day.isoformat(),ref,offset),
                                                  'data':binary,'valid':sum(x['status']=='VALIDO' for x in report['snapshots']),
                                                  'total':len(report['snapshots'])}
        except Exception as exc:
            st.error(f'Impossibile preparare il report: {type(exc).__name__}: {str(exc)[:180]}')
    ready=st.session_state.get('ai_report_ready')
    if ready and ready['key']==(week,day.isoformat(),ref,offset):
        st.success(f"Report pronto: {ready['valid']} snapshot validi su {ready['total']} acquisiti.")
        st.download_button('Scarica report JSON + PDF (ZIP)',data=ready['data'],
                           file_name=f'Serafini_SPX_AI_{day.isoformat()}.zip',mime='application/zip',
                           key=f'ai_download_{day}')

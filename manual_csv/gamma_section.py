"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import json
import math
from datetime import date
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from .net_gamma import profile, read_snapshot, visual_profile, gamma_component_totals
from feed_quality import timestamp_state
from volatility_levels import weekly_expiry
from gamma_scale import profile_gamma_bounds, gamma_components
from .serafini_sigma import serafini_cash_levels, add_sigma_markers
from operational_levels import find_candidates
from es_spx_basis import download_and_calculate
from decimal import Decimal, ROUND_HALF_UP


@st.cache_data(ttl=86400, max_entries=24, show_spinner=False)
def _selected_snapshot(week, file_name, _store):
    """Snapshot immutabile: evita nuovi download e parsing sul semplice rerun UI."""
    return read_snapshot(_store.download(f'{week}/snapshots/{file_name}'))


@st.cache_data(ttl=86400, max_entries=90, show_spinner=False)
def _snapshot_cboe_stamp(week, file_name, _store):
    """Un solo timestamp storico aggiuntivo, cache 5 minuti, solo lettura."""
    item = read_snapshot(_store.download(f"{week}/snapshots/{file_name}"))
    return item.get("timestamp")








@st.cache_data(ttl=3600, max_entries=36, show_spinner=False)
def _snapshot_log_index(week, log_names, _store):
    """Un solo passaggio sui log Supabase per TUTTE le fotografie.

    Prima, le due funzioni di etichetta scaricavano ogni log per ogni
    fotografia (O(snapshot * log)). Ora i log sono letti una sola volta;
    la cache si invalida se appare un nuovo log e dopo massimo 120 s.
    Le regole di scelta dell'ora (primo download valido) restano identiche.
    """
    rome=ZoneInfo('Europe/Rome')
    earliest={}
    latest={}
    for log_name in log_names:
        try:
            item=json.loads(_store.download(f'{week}/logs/{log_name}'))
            if item.get('status') not in ('NUOVO','INVARIATO'):
                continue
            file_path=str(item.get('file') or '')
            if not file_path.startswith(f'{week}/snapshots/'):
                continue
            raw=item.get('download_rome') or item.get('download_utc')
            if not raw:
                continue
            dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
            if dt.tzinfo is None:
                continue
            dt=dt.astimezone(rome)
            if file_path not in earliest or dt < earliest[file_path][0]:
                earliest[file_path]=(dt, str(item.get('scheduled_slot','')).strip())
            if file_path not in latest or dt > latest[file_path]:
                latest[file_path]=dt
        except (KeyError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
            continue
    result={}
    for path, (dt,slot) in earliest.items():
        if slot == '30 7 * * 1-5':
            label='Mattina 07:30'
        elif slot == '30 13 * * 1-5':
            label=('Pre-USA 13:30 · ufficiale (11/11)' if dt.date() == date(2026, 11, 11)
                   else '13:30 (studio storico)' if dt.date() < date(2026, 11, 11)
                   else '13:30 · cron ufficiale ritirato')
        elif slot == '46 14 * * 1-5':
            label='07:46 Chicago · ufficiale (slot 14:46)'
        elif slot == '0 17 * * 1-5':
            label='USA · 10:00 Chicago (17:00 ordinario)'
        elif slot == '46 15 * * 1-5':
            label='Pomeriggio 15:46'
        elif slot == '50 15 * * 1-5':
            label='Pomeriggio · 08:50 Chicago (storico)'
        elif slot == '16 16 * * 1-5':
            label='Ufficiale · 09:16 Chicago (slot 16:16)'
        elif slot == '46 17 * * 1-5':
            label='Ufficiale · 10:46 Chicago (slot 17:46)'
        elif slot == '30 20 * * 1-5':
            label='Sera · 13:30 Chicago'
        elif slot == '30 15 * * 1-5':
            label='Pomeriggio 15:30 (storico)'
        elif slot == '45 7 * * 1-5':
            label='Recupero mattina 07:45'
        elif slot == '45 15 * * 1-5':
            label='Recupero pomeriggio 15:45'
        elif slot.lower() in ('','manuale','manual','test','workflow_dispatch'):
            label=f'Manuale {dt:%H:%M}'
        else:
            label=f'Download {dt:%H:%M}'
        result[path]=(label,dt.strftime('%H:%M'),latest[path])
    return result


def _latest_downloaded_snapshot(store, week, snaps, logs):
    """Choose the latest successful download, not the CBOE timestamp in filenames.

    Uses the cached acquisition-log index; selecting never downloads raw chains.
    """
    names = {item['name'] for item in snaps if item.get('name','').startswith('SPX_CBOE_')}
    if not names:
        return None
    index = _snapshot_log_index(week, tuple(item['name'] for item in logs), store)
    registered = [
        (index[f'{week}/snapshots/{name}'][2], name)
        for name in names if f'{week}/snapshots/{name}' in index
    ]
    # Legacy/local archives without logs: preserve previous filename fallback,
    # without claiming the true acquisition time is known.
    return max(registered)[1] if registered else max(names)


def _sync_snapshot_selection(store, week, snaps, logs):
    """Follow latest by default, preserving explicit manual historical selection."""
    names = sorted((item['name'] for item in snaps), reverse=True)
    if not names:
        return names, None
    selected_key = f'manual_csv_serafini_selected_snapshot_{week}'
    follow_key = f'manual_csv_serafini_follow_latest_{week}'
    latest = _latest_downloaded_snapshot(store, week, snaps, logs) or names[0]
    if st.session_state.pop(f'manual_csv_spx_refresh_latest_{week}', False):
        st.session_state[follow_key] = True
    if st.session_state.get(follow_key, True) or st.session_state.get(selected_key) not in names:
        st.session_state[selected_key] = latest
    return names, latest




def _acquisition_label(store, week, logs, file_name):
    """Etichetta dal PRIMO registro valido, mai dal timestamp Cboe."""
    idx=_snapshot_log_index(week,tuple(x['name'] for x in logs),store)
    return idx.get(f'{week}/snapshots/{file_name}',
                   ('Rilevamento non identificato',None))[0]


def _snapshot_download_clock(store, week, logs, file_name):
    """Orario di download a Roma; stessa cache per tutta la settimana."""
    idx=_snapshot_log_index(week,tuple(x['name'] for x in logs),store)
    return idx.get(f'{week}/snapshots/{file_name}',(None,None))[1]


def _csv_cboe_capture_label(snapshot):
    """Ora della fotografia Cboe CSV, non dell'operazione di download.

    Il CSV ha un timestamp UTC verificato dal parser. Un file importato
    manualmente non ha registri di download: non utilizzare mai le vecchie
    etichette Supabase per rappresentarlo.
    """
    provenance = snapshot.get('_import_provenance') or {}
    if (provenance.get('source') != 'CBOE_CSV_MANUAL_USER_EXPORT'
            or provenance.get('timestamp_utc_converted') is not True):
        return None
    try:
        stamp = datetime.strptime(
            str(snapshot['timestamp']), '%Y-%m-%d %H:%M:%S'
        ).replace(tzinfo=ZoneInfo('UTC'))
        return f'Fotografia CSV Cboe · ore {stamp.astimezone(ZoneInfo("Europe/Rome")):%H:%M} Roma'
    except (KeyError, ValueError, TypeError):
        return 'Fotografia CSV Cboe · ora fonte non verificabile'


def render(store, week, snaps, logs, *, show_heading=True, selector_external=False):
    if show_heading:
        st.header('NET GAMMA · formula Serafini')
    if not snaps:
        st.info('Nessuno snapshot disponibile nella settimana selezionata.')
        return
    # When the selector was already rendered in app.py, never write its widget
    # session_state again in this same Streamlit run (widget-state restriction).
    if selector_external:
        names = sorted((item['name'] for item in snaps), reverse=True)
    else:
        names, _ = _sync_snapshot_selection(store, week, snaps, logs)
    selection_key = f'manual_csv_serafini_selected_snapshot_{week}'
    # La selezione resta nello stato della sessione; il controllo viene mostrato
    # immediatamente prima delle opzioni di scala dei profili.
    choice = st.session_state[selection_key]
    try:
        snap = _selected_snapshot(week, choice, store)
    except Exception as exc:
        st.error(f'Impossibile leggere lo snapshot: {type(exc).__name__}')
        return
    timestamp = str(snap.get('timestamp') or '')
    csv_capture_label = _csv_cboe_capture_label(snap)
    acquisition_label = (csv_capture_label if csv_capture_label is not None
                         else _acquisition_label(store, week, logs, choice))
    # Il timestamp originale Cboe NON prova l'ora di download del file.
    download_clock = (None if csv_capture_label is not None
                      else _snapshot_download_clock(store, week, logs, choice))
    # Confronto soltanto con il file precedente della STESSA giornata;
    # non interpreta il fuso del timestamp Cboe e non blocca il profilo.
    day_prefix = choice[:len('SPX_CBOE_') + 8]
    same_day_names = [name for name in names if name.startswith(day_prefix)]
    position = same_day_names.index(choice)
    preceding = same_day_names[position + 1] if position + 1 < len(same_day_names) else None
    if preceding is None:
        quality = timestamp_state(timestamp)
    else:
        try:
            previous_stamp = _snapshot_cboe_stamp(week, preceding, store)
            quality = timestamp_state(timestamp, previous_stamp, has_previous=True)
        except Exception:
            quality = "NON VERIFICABILE"
    quality_label = {
        "PRIMO RILEVAMENTO": "primo rilevamento",
        "DIVERSO": "timestamp diverso",
        "RIPETUTO": "timestamp ripetuto",
        "NON VERIFICABILE": "non verificabile",
    }[quality]
    # Si usa la data dello snapshot per gli esempi e per impedire look-ahead.
    try:
        day = date.fromisoformat(choice.split('SPX_CBOE_')[1][:8][:4] + '-' + choice.split('SPX_CBOE_')[1][4:6] + '-' + choice.split('SPX_CBOE_')[1][6:8])
    except Exception:
        st.error('Data dello snapshot non riconosciuta nel nome del file.')
        return
    previous = snap['data'].get('prev_day_close')
    @st.cache_data(ttl=3600, show_spinner=False)
    def _auto_basis(target_day):
        result = download_and_calculate(target_day)
        # Non memorizziamo frame grafici nel widget di selezione.
        result.pop('bars', None)
        return result

    st.markdown('**Differenziale ES − SPX (sessione precedente)**')
    basis_key = f'manual_csv_basis_override_{day.isoformat()}'
    attempt_key = f'manual_csv_basis_auto_attempted_{day.isoformat()}'
    if 'manual_csv_manual_csv_auto_basis_results' not in st.session_state:
        st.session_state.manual_csv_manual_csv_auto_basis_results = {}

    def _apply_auto_basis():
        result = _auto_basis(day)
        st.session_state.manual_csv_manual_csv_auto_basis_results[day.isoformat()] = result
        st.session_state[basis_key] = str(int(Decimal(str(result['basis'])).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP)))
        return result

    recalc_error_key = f'manual_csv_basis_recalc_error_{day.isoformat()}'

    def _recalculate_basis():
        # Callback Streamlit: viene eseguita PRIMA della creazione dei widget
        # nel rerun. Evita la modifica di basis_key dopo st.text_input.
        try:
            with st.spinner('Acquisizione e confronto barre ES/SPX a 15 minuti...'):
                _apply_auto_basis()
            st.session_state.pop(recalc_error_key, None)
            st.session_state.pop(f'manual_csv_basis_auto_error_{day.isoformat()}', None)
        except Exception as exc:
            # Non sovrascrivere il valore inserito manualmente in caso di errore.
            st.session_state[recalc_error_key] = str(exc)[:250]

    # Una sola acquisizione automatica per giorno e sessione Streamlit.
    # Non sovrascrivere un valore gia' modificato manualmente.
    if attempt_key not in st.session_state:
        st.session_state[attempt_key] = True
        if not str(st.session_state.get(basis_key, '')).strip():
            try:
                with st.spinner('Calcolo automatico differenziale ES − SPX...'):
                    _apply_auto_basis()
            except Exception as exc:
                st.session_state.manual_csv_manual_csv_auto_basis_results[day.isoformat()] = None
                st.session_state[f'manual_csv_basis_auto_error_{day.isoformat()}'] = str(exc)[:250]

    # v41: Streamlit puo' conservare il risultato automatico ma lasciare vuoto
    # il widget dopo un cambio snapshot/rerun. Riallineiamo lo stato PRIMA
    # dell'istanziazione del text_input, senza sovrascrivere valori non vuoti
    # (anche se modificati manualmente dall'utente).
    cached_basis = st.session_state.manual_csv_manual_csv_auto_basis_results.get(day.isoformat())
    if cached_basis is not None and not str(st.session_state.get(basis_key, '')).strip():
        st.session_state[basis_key] = str(int(Decimal(str(cached_basis['basis'])).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP)))

    col1, col2, col3 = st.columns([1.15, 1.45, 0.65], gap='small')
    with col1:
        ref = st.number_input('Chiusura SPX precedente', min_value=0.01,
                              value=float(previous or snap['data']['current_price']),
                              key=f'manual_csv_reference_{choice}',
                              step=0.25, format='%.4f',
                              help='Riferimento per i 50 strike. Verificare con la chiusura cash ufficiale.')
    with col2:
        offset_txt = st.text_input('Differenziale ES − SPX applicato in automatico e modificabile',
                                   key=basis_key,
                                   placeholder='Es. 47',
                                   help='Calcolato automaticamente alla prima apertura; puoi correggerlo. Vuoto = grafico SPX.')
    with col3:
        # Allinea il pulsante all'altezza dei campi, anziché alle intestazioni.
        st.markdown('<div style="height: 29px"></div>', unsafe_allow_html=True)
        st.button('Ricalcola ES − SPX', key=f'manual_csv_auto_basis_{day}',
                  on_click=_recalculate_basis, width="stretch")
    if st.session_state.get(recalc_error_key):
        st.warning('Ricalcolo non disponibile: ' +
                   st.session_state[recalc_error_key] +
                   '. Mantengo il valore inserito.')
    if st.session_state.get(f'manual_csv_basis_auto_error_{day.isoformat()}'):
        st.caption('Calcolo automatico non disponibile: ' +
                   st.session_state[f'manual_csv_basis_auto_error_{day.isoformat()}'] +
                   '. Puoi inserire manualmente il differenziale.')
    auto_result = st.session_state.manual_csv_manual_csv_auto_basis_results.get(day.isoformat())
    if auto_result:
        st.caption(f"Proposta automatica **{auto_result['basis']:.2f} punti → arrotondata a {int(Decimal(str(auto_result['basis'])).quantize(Decimal('1'), rounding=ROUND_HALF_UP))} punti** · "
                   f"seduta {auto_result['reference_date']} · {auto_result['bar_count']} barre a 15 minuti "
                   f"· min {auto_result['minimum']:.2f} / max {auto_result['maximum']:.2f} "
                   f"· ultimo {auto_result['last']:.2f}. Fonte: {auto_result['source']}.")
    try:
        offset = None if not offset_txt.strip() else float(offset_txt.replace(',', '.'))
    except ValueError:
        st.error('Differenziale non numerico'); return
    weekly_exp = weekly_expiry(day)
    if day.weekday() >= 5:
        st.error('Snapshot in giorno non operativo: scegliere manualmente una giornata valida.'); return
    st.caption(f'0DTE: {day.isoformat()} · Weekly: {weekly_exp.isoformat()} (lun–gio venerdì corrente; venerdì successivo). In caso di festività controllare la scadenza effettiva.')
    computed_profiles = {}
    candidate_0dte = None
    if day.weekday() == 4:
        st.info('Venerdì: il profilo 0DTE è quello di oggi, mentre il Weekly usa il venerdì SUCCESSIVO (conferma diretta di Serafini).')
    # Build both profiles before drawing so the same numeric range is available.
    profile_errors = {}
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        try:
            computed_profiles[title] = profile(snap, expiry, ref)
        except ValueError as exc:
            profile_errors[title] = str(exc)
    # Nomi tecnici conservati come valori interni; il menu mostra gli orari
    # di acquisizione ricavati dai registri, non dal timestamp originale Cboe.
    snapshot_menu_labels = {}
    for snapshot_name in names:
        date_code = snapshot_name[len('SPX_CBOE_'):len('SPX_CBOE_') + 8]
        date_display = (f'{date_code[6:8]}/{date_code[4:6]}/{date_code[:4]}'
                        if len(date_code) == 8 and date_code.isdigit() else 'Data n.d.')
        if csv_capture_label is not None and snapshot_name == choice:
            slot_label, actual_clock = csv_capture_label, None
            clock_label = ''
        else:
            slot_label = _acquisition_label(store, week, logs, snapshot_name)
            actual_clock = _snapshot_download_clock(store, week, logs, snapshot_name)
            clock_label = (f' · download {actual_clock} Roma'
                           if actual_clock else ' · ora download non disponibile')
        snapshot_menu_labels[snapshot_name] = (
            f'{date_display} · {slot_label}{clock_label} · {snapshot_name}'
        )
    if selector_external:
        st.caption(f'Rilevamento selezionato: {acquisition_label} · '
                   f'Timestamp Cboe: {timestamp or "n.d."} · '
                   f'Cboe: {quality_label} · file {choice}')
    else:
        col_snapshot, col_snapshot_info = st.columns(
            [1.25, 2.75], gap='small', vertical_alignment='bottom')
        with col_snapshot:
            st.selectbox('Snapshot originale Cboe', names, key=selection_key,
                         format_func=lambda snapshot_name: snapshot_menu_labels.get(snapshot_name, snapshot_name))
        with col_snapshot_info:
            st.caption(f'Rilevamento selezionato: {acquisition_label} · '
                       f'Timestamp Cboe: {timestamp or "n.d."} · '
                       f'Cboe: {quality_label} · file {choice}')
    st.markdown('**Priorità operativa alle 0DTE:** Serafini richiama un peso numerico del '
                '**60% per le 0DTE** e del **23% per le Weekly** e, in caso di dubbio, '
                'privilegia i livelli 0DTE più marcati.')
    show_sigma_90 = st.checkbox(
        'Mostra StdDev 90 — solo LONG (Pine v1.4)',
        value=False, key='manual_csv_sigma_show_90_long_v14',
        help='Disattivata di default come nel Pine. Le due bande 350 restano sempre visibili.'
    )
    # Deviazioni Serafini cash M15, indipendenti dal riferimento SPX e dal
    # differenziale ES-SPX: prezzi di riferimento del future ES=F.
    # Un solo tentativo con cache; nessuna deviazione stimata se mancano M15.
    try:
        sigma_levels = serafini_cash_levels(day) if offset is not None else None
        sigma_error = None
    except Exception as exc:
        sigma_levels = None
        sigma_error = f'{type(exc).__name__}: {str(exc)[:115]}'
    # Estensione INTELLIGENTE, solo grafica. La spunta resta ON per default
    # ma non aggiunge strike se i 25+25 già coprono la fascia richiesta.
    # Calcolo Serafini, C1/P1 e valori riepilogativi rimangono identici.
    show_visual_extension = st.checkbox(
        'Estensione intelligente NET GAMMA — solo grafici',
        value=True, key='manual_csv_gamma_visual_extension_v1',
        help='ON di default. Copertura sui prezzi noti della fotografia '
             'e sul range delle ultime 3-5 sedute RTH M15 complete ES=F '
             '(preferibilmente 5), con basis ES-SPX attuale per la sola '
             'proiezione grafica. Se non verificabile: sicurezza ±5% SPX. '
             'Fino a 200 strike Cboe reali/scadenza; 25+25 originali, '
             'calcoli Serafini, C1/P1 e GEX invariati.'
    )
    known_low = known_high = None
    range_reference = 'Chiusura SPX precedente e spot fotografia Cboe'
    # Senza le 5 RTH complete e un basis ES-SPX utilizzabile, i soli
    # 50 strike possono occultare escursioni importanti (es. 23/11).
    # Fallback cautelativo: fascia ±5%, usando SOLO strike reali del CSV.
    # Non afferma che il prezzo sia realmente stato in quella fascia.
    fallback_buffer = .05
    if sigma_levels and offset is not None and math.isfinite(float(offset)):
        last_session = sigma_levels.get('recent_rth_end_chicago')
        recent_count = sigma_levels.get('recent_rth_sessions')
        recent_low = sigma_levels.get('recent_rth_low_es')
        recent_high = sigma_levels.get('recent_rth_high_es')
        if (recent_low is not None and recent_high is not None
                and isinstance(recent_count, int) and 3 <= recent_count <= 5
                and last_session is not None):
            candidate_low = float(recent_low) - float(offset)
            candidate_high = float(recent_high) - float(offset)
            # Solo sessioni CHIUSE prima del giorno CSV; mai includere
            # la seduta corrente o prezzi futuri per gli storici.
            if (date.fromisoformat(str(last_session)) < day
                    and 0 < candidate_low <= candidate_high
                    and abs(candidate_low / ref - 1.0) < .15
                    and abs(candidate_high / ref - 1.0) < .15):
                known_low, known_high = candidate_low, candidate_high
                fallback_buffer = None  # marginazione adattiva del motore
                range_reference = (
                    f'{recent_count} sedute RTH complete ES=F '
                    f'{sigma_levels["recent_rth_start_chicago"]}–'
                    f'{last_session} · minimo {float(recent_low):,.2f} '
                    f'· massimo {float(recent_high):,.2f} ES '
                    '(basis attuale costante; solo grafica)'
                )
    visual_profiles = {}
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        if title not in computed_profiles:
            continue
        core = computed_profiles[title]
        if not core.get('rows') or not show_visual_extension:
            visual_profiles[title] = core
            continue
        try:
            visual_profiles[title] = visual_profile(
                snap, core, expiry, ref, snap['data'].get('current_price'),
                max_strikes=200, buffer_pct=fallback_buffer,
                known_low=known_low, known_high=known_high,
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            st.caption(f'Estensione {title} non disponibile: {exc}. '
                       'Mostro soltanto i 50 strike Serafini originali.')
            visual_profiles[title] = core
    if show_visual_extension:
        if fallback_buffer is None:
            st.caption(
                'Estensione intelligente: **' + range_reference + '**. '
                'Margine adattivo di sicurezza 0,25–1% SPX; '
                '50 strike se sufficienti, fino a 200 strike Cboe '
                'autentici/scadenza quando il range multi-seduta lo richiede.'
            )
        else:
            st.caption(
                'Estensione di sicurezza **±5% dalla fascia del riferimento '
                'SPX e dello spot Cboe**, perché il range delle ultime '
                '3–5 RTH complete ES=F non è verificabile (o manca il '
                'differenziale ES-SPX). Si usano solo strike presenti '
                'nel CSV, max 200/scadenza. Non è una misurazione del '
                'range effettivamente percorso.'
            )
    # Riepilogo CANONICO: solo profili Serafini 25+25, non gli strike
    # selezionati per la grafica. Il valore NON cambia attivando
    # l'estensione, la sua copertura o il suo limite (140/200).
    core_summary = gamma_component_totals(
        computed_profiles.values(), require_core=True
    )
    if core_summary is not None:
        net_summary = core_summary['net']
        call_summary = core_summary['call']
        put_summary = core_summary['put']

        def _gamma_it(value, signed=False):
            formatted = f'{value:+,.2f}' if signed else f'{value:,.2f}'
            return formatted.replace(',', '_').replace('.', ',').replace('_', '.')

        net_col, call_col, put_col = st.columns(3)
        with net_col:
            net_color = '#f05252' if net_summary < 0 else '#f1f5f9'
            st.markdown(
                '<div style="font-size:0.88rem;color:#d1d5db;'
                'margin-bottom:0.35rem">NET GAMMA · Call − Put · 25+25</div>'
                f'<div style="font-size:1.85rem;line-height:1.25;'
                f'font-weight:400;color:{net_color};font-variant-numeric:tabular-nums">'
                f'{_gamma_it(net_summary, signed=True)}</div>',
                unsafe_allow_html=True,
            )
        call_col.metric('GAMMA CALL (+) · 25+25',
                        _gamma_it(call_summary, signed=True))
        put_col.metric('GAMMA PUT (−) · 25+25',
                       '−' + _gamma_it(put_summary))
        st.caption(
            f"**Totali originali Serafini:** {core_summary['profiles']} scadenze, "
            f"{core_summary['strikes']} strike (25 sopra e 25 sotto per scadenza). "
            "L'estensione dinamica modifica solo i grafici, mai questi totali."
        )
    shared_bounds = profile_gamma_bounds(visual_profiles.values())
    st.caption('Tooltip: componente Call positiva, componente Put negativa e saldo netto '
               'della formula Serafini (OI + Volume) × Gamma; non valori monetari delle posizioni.')
    independent_scale = st.checkbox(
        'Ingrandisci ogni profilo con scala indipendente', value=False,
        key='manual_csv_gamma_independent_scale_v1',
        help='Disattivato: stessa scala per confrontare 0DTE e Weekly. '
             'Attivato: le lunghezze NON sono confrontabili tra i due profili.')
    profile_cols = st.columns(2, gap='small')
    for (title, expiry), profile_col in zip(
            [('0DTE', day), ('Weekly', weekly_exp)], profile_cols):
        with profile_col:
            if title in profile_errors:
                st.error(f'Impossibile costruire il profilo {title}: {profile_errors[title]}')
                continue
            result = computed_profiles[title]
            if title == '0DTE' and result['rows']:
                candidate_0dte = find_candidates(result['rows'], ref)
            capture_info = (
                csv_capture_label if csv_capture_label is not None else
                (f'{acquisition_label} · download ore {download_clock} Roma'
                 if download_clock else f'{acquisition_label} · ora download non disponibile')
            )
            # Data e ora della fotografia evidenziate: nessuna conversione
            # del timestamp Cboe privo di fuso, né modifica ai Gamma.
            from html import escape as _html_escape
            st.markdown(
                f'<div style="font-size:1.3rem;line-height:1.32;'
                f'font-weight:700;margin:0 0 0.4rem">'
                f'{_html_escape(title)} — RILEVAMENTO '
                f'<span style="color:#55d5f6">'
                f'{day:%d/%m/%Y} · {_html_escape(capture_info)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption(f'Scadenza opzioni: {expiry:%d/%m/%Y} · Timestamp originale Cboe: {timestamp} (UTC da intestazione CSV)')
            if not result['rows']:
                st.warning('Nessun contratto trovato per questa scadenza nello snapshot: nessun risultato inventato.')
                continue
            visual = visual_profiles.get(title, result)
            extra_count = len(visual.get('rows', [])) - len(result['rows'])
            st.caption(f"{result['contracts']} contratti · {result['available_strikes']} strike disponibili · "
                       f"{len(result['rows'])} originali (25+25)"
                       + (f" + {extra_count} estesi solo per il grafico" if extra_count else ""))
            if show_visual_extension and visual.get('requested_low') is not None:
                covered = bool(visual.get('coverage_complete'))
                omitted = visual.get('sampled_strikes', 0)
                shift = float(offset) if offset is not None and math.isfinite(offset) else 0.0
                axis = 'ES' if offset is not None and math.isfinite(offset) else 'SPX'
                if visual.get('decision') == '50_sufficienti':
                    st.caption(
                        f"Estensione intelligente: i 50 strike originali "
                        f"coprono già il range {axis} e il margine; nessuno "
                        f"strike aggiuntivo nel grafico o nel Pine."
                    )
                else:
                    st.caption(
                        f"Copertura NET GAMMA grafica {axis} "
                        f"{visual['actual_low'] + shift:,.2f}–"
                        f"{visual['actual_high'] + shift:,.2f}"
                        f" · obiettivo {visual['requested_low'] + shift:,.2f}–"
                        f"{visual['requested_high'] + shift:,.2f}"
                        + (" · estremi coperti" if covered else
                           " · fascia non interamente coperta dai dati Cboe")
                        + (f" · {omitted} strike Cboe tralasciati per limite "
                           "grafico, senza interpolazioni" if omitted else "")
                    )
            records = [{**r, 'strike_es': r['strike_spx'] + offset if offset is not None else None} for r in visual['rows']]
            import pandas as pd
            frame = pd.DataFrame(records)
            # Slide 144: profilo NET GAMMA, non componenti separate.
            # Il lato della barra deriva esclusivamente dal segno del NET GAMMA calcolato.
            # Nessun Gamma Flip imposto o stimato arbitrariamente.
            import plotly.graph_objects as go
            y = frame['strike_es' if offset is not None else 'strike_spx'].tolist()
            net = frame['net_gamma'].astype(float).tolist()
            colors = ['#2dbb72' if v >= 0 else '#e45757' for v in net]
            fig = go.Figure()
            fig.add_bar(
                x=net, y=y, orientation='h', name='NET GAMMA',
                marker_color=colors,
                customdata=[gamma_components(row) for row in records],
                hovertemplate=(
                    'Strike %{y:,.2f}<br>NET GAMMA: %{x:,.3f}'
                    '<br>Componente Call (+): %{customdata[0]:,.3f}'
                    '<br>Componente Put (−): %{customdata[1]:,.3f}<extra></extra>'
                ),
            )
            fig.update_layout(
                height=max(520, min(1250, len(y) * 12)),
                margin=dict(l=15, r=20, t=40, b=30),
                title='NET GAMMA per strike — positivo a destra / negativo a sinistra',
                xaxis=dict(title='NET GAMMA (unità della formula Serafini)', zeroline=True,
                           range=(profile_gamma_bounds([visual]) if independent_scale else shared_bounds),
                           fixedrange=not independent_scale,
                           zerolinewidth=1, zerolinecolor='#b7bec8'),
                yaxis=dict(title='Strike ES' if offset is not None else 'Strike SPX',
                           tickmode='array',
                           tickvals=y[::max(1, math.ceil(len(y) / 45))],
                           ticktext=[f'{v:g}' for v in y[::max(1, math.ceil(len(y) / 45))]],
                           type='linear'),
                showlegend=False,
                template='plotly_dark',
            )
            fig.add_vline(x=0, line_width=1, line_color='#b7bec8')
            # Riferimento ES comune ai due istogrammi: Close SPX precedente
            # (stesso ref usato per selezionare gli strike) + basis ES-SPX.
            # Non alterare l'ordinamento, le barre o il NET GAMMA.
            if offset is not None and math.isfinite(offset):
                previous_close_es = float(ref) + offset
                fig.add_hline(
                    y=previous_close_es, line_dash='dash',
                    line_width=1.5, line_color='#f0cc65',
                )
                # Guide percentuali rispetto allo STESSO Close prec. ES.
                # Sono solamente riferimenti grafici; il NET GAMMA resta intatto.
                for pct in (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5):
                    target = previous_close_es * (1.0 + pct / 100.0)
                    emphasis = abs(pct) == 1.0
                    line_color = ('rgba(205,215,230,0.62)' if emphasis
                                  else 'rgba(195,205,220,0.46)')
                    fig.add_hline(
                        y=target, line_dash='dot',
                        line_width=1.8 if emphasis else 1.2,
                        line_color=line_color,
                    )
                    fig.add_annotation(
                        x=0.995, xref='paper', y=target, yref='y',
                        text=f'{pct:+.1f}%'.replace('.', ','),
                        showarrow=False, xanchor='right', yanchor='middle',
                        font=dict(
                            color=('rgba(225,232,240,0.92)' if emphasis
                                   else 'rgba(210,218,228,0.82)'),
                            size=10,
                        ),
                        bgcolor='rgba(13,17,23,0.78)', borderpad=1,
                    )
                # Il riferimento principale rimane visivamente prioritario.
                fig.add_annotation(
                    x=0.995, xref='paper', y=previous_close_es, yref='y',
                    text='Close prec.', showarrow=False,
                    xanchor='right', yanchor='bottom',
                    font=dict(color='#f0cc65', size=11),
                    bgcolor='rgba(13,17,23,0.85)', borderpad=2,
                )
            # SOLO 0DTE: due segmenti 350, terzo 90 solo quando attivato.
            if title == '0DTE' and sigma_levels is not None:
                _, clipped_sigma = add_sigma_markers(
                    fig, sigma_levels, y, show_90=show_sigma_90)
            else:
                clipped_sigma = []
            st.plotly_chart(fig, width="stretch", key=f'manual_csv_gamma_net_{title}_{choice}')
            if title == '0DTE':
                if sigma_error:
                    st.caption('Deviazione Serafini M15 non disponibile: ' + sigma_error)
                elif offset is None:
                    st.caption('Deviazione Serafini ES M15: richiede differenziale ES-SPX per allineare il grafico.')
                elif sigma_levels and sigma_levels.get('status') != 'VALIDO':
                    st.caption('Deviazione Serafini M15 non disponibile: ' +
                               str(sigma_levels.get('reason', 'storico insufficiente')) +
                               '. Nessun livello ipotizzato.')
                elif sigma_levels and sigma_levels.get('status') == 'VALIDO':
                    st.caption(
                        'StdDev Serafini v1.4 — ES=F M15 RTH Chicago 08:30–15:15; '
                        '350/90 chiusure INCLUSA la candela 15:00–15:15, '
                        'livelli congelati per la seduta successiva. '
                        + f"L350 {sigma_levels['L350']:.2f} · "
                        + f"S350 {sigma_levels['S350']:.2f}"
                        + (f" · L90 LONG {sigma_levels['L90']:.2f}" if show_sigma_90 else " · L90 OFF")
                        + ('. Fuori dal range visualizzato: ' + ', '.join(clipped_sigma)
                           if clipped_sigma else '.')
                        + ' Fonte Yahoo ES=F: differenze possibili rispetto a TV ES1!.'
                    )

    # Confronto facoltativo SPX/ES come su Nasdaq. Fetch prezzi solo a toggle ON.
    # La proiezione y usa il differenziale gia' calcolato; Gamma invariato.
    from .gamma_price_compare import render_optional_compare
    render_optional_compare(
        label="S&P 500", key="manual_csv_spx", yahoo_ticker="ES=F",
        target_day=day, groups=visual_profiles,
        strike_field="strike_spx", gamma_field="net_gamma",
        source_name="SPX", price_name="ES",
        price_scale=1.0, price_shift=offset if offset is not None else 0.0,
        reference_close=ref, snapshot_doc=snap,
        available=offset is not None,
        unavailable_reason="Per confrontare le candele ES serve il "
                           "differenziale ES-SPX verificato.",
    )

    # Guida interpretativa: nessun effetto sui calcoli o sugli export.
    # Visibile sotto entrambi i grafici Net Gamma, chiusa per default.
    with st.expander('COME LEGGERE E USARE IL NET GAMMA SERAFINI', expanded=False):
        st.markdown("""
**Che cosa rappresenta**

Il Net Gamma Serafini è una mappa che ci aiuta a individuare i livelli di prezzo più sensibili, dove le opzioni possono reagire maggiormente.

Quando molte opzioni presentano una sensibilità significativa in determinati strike, l'esposizione complessiva al Delta può cambiare rapidamente mentre il sottostante si muove.

Pertanto, questi livelli sono interessanti non perché il prezzo debba necessariamente fermarsi o invertire, ma perché potrebbero essere associati alla necessità di aggiustare le coperture, se esistono posizioni effettive che richiedono hedging.

Chi gestisce queste opzioni potrebbe quindi avere bisogno di comprare o vendere strumenti collegati al sottostante per mantenere equilibrate le proprie posizioni.

**Il Net Gamma aiuta a individuare dove osservare con maggiore attenzione il comportamento del prezzo, ma non stabilisce da solo in quale direzione il mercato si muoverà.**

**Come leggere il grafico**

- **Barre verdi:** prevalenza della componente Call nel calcolo Serafini.
- **Barre rosse:** prevalenza della componente Put nel calcolo Serafini.
- **Barre più lunghe:** maggiore sensibilità complessiva stimata in corrispondenza di quel livello.
- **Barre che cambiano tra due rilevamenti:** indicano che la struttura delle opzioni sta cambiando, ma non necessariamente che gli operatori stiano aprendo o chiudendo posizioni.

**Perché il Net Gamma cambia durante la giornata**

La sensibilità delle opzioni non è fissa. Può cambiare perché:

- Il sottostante sale o scende.
- Si avvicina la scadenza delle opzioni, soprattutto quelle 0DTE.
- Cambia la volatilità attesa dal mercato.
- Cambiano i volumi e le posizioni presenti sulle opzioni.
        """)

    # Manual CSV stays separate from the operational 09:00 context and retired LAB.
    asia_c1 = asia_p1 = asia_level_context = None

    asia_panel = {
        'day': day, 'c1_es': asia_c1, 'p1_es': asia_p1,
        'level_context': asia_level_context,
        # Exact same ES-SPX basis already used for NET GAMMA and Pine.
        # A missing basis must NOT be silently replaced by zero.
        'es_spx_basis': offset if offset is not None and math.isfinite(offset) else None,
    }

    if offset is not None and all(computed_profiles.get(k, {}).get('rows') for k in ('0DTE', 'Weekly')):
        from .pine_export import make_pine
        groups = {}
        for k in ('0DTE', 'Weekly'):
            groups[k] = dict(computed_profiles[k], display_rows=visual_profiles[k]['rows'])
        script = make_pine(groups['0DTE'], groups['Weekly'], offset, day.isoformat(),
                           timestamp, current_only=True)
        # I calcoli NET GAMMA restano invariati: solo overlay GEX opzionale.
        from .pine_ui import prepare_pine_with_csv_gex
        script = prepare_pine_with_csv_gex(
            script, 'spx', source_ticker='SPX',
            price_factor=1.0, price_shift=offset,
        )
        pine_download_col, pine_copy_col = st.columns(2, gap='small')
        with pine_download_col:
            st.download_button('Scarica Pine SPX · fotografia corrente', script,
                               file_name=f'SPX_NET_GAMMA_CSV_{day}.pine',
                               key='manual_csv_pine_download_control')
        with pine_copy_col:
            with st.container(key='manual_csv_pine_copy_container'):
                with st.expander('Visualizza e copia codice Pine', expanded=False):
                    st.caption('Usa l’icona Copia in alto a destra del codice per incollarlo in TradingView.')
                    st.code(script, language=None, wrap_lines=False)

    return asia_panel

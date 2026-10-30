"""Dashboard pubblicamente raggiungibile, dati privati, ruoli admin/lettore.
NON ospitare chiavi nell'app o nel repository: solo Streamlit Secrets.
"""
import gzip
import hashlib
import hmac
import json
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from storage import Store

st.set_page_config(page_title='Serafini SPX — Archivio', page_icon='📊', layout='wide')


def format_download_rome(value):
    """Presentazione soltanto: i timestamp sorgente rimangono invariati."""
    if not value:
        return '—'
    try:
        # Gli snapshot attuali contengono timestamp ISO UTC con +00:00.
        stamp = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return f'{value} (fuso non indicato)'
        rome = stamp.astimezone(ZoneInfo('Europe/Rome'))
        return rome.strftime('%d/%m/%Y %H:%M:%S')
    except (TypeError, ValueError, OverflowError):
        return str(value)


@st.cache_data(show_spinner=False, ttl=1800, max_entries=250)
def _cached_quality_from_file(path, _store):
    # Solo lo stato di validita': i dati grezzi NON entrano nella cache.
    try:
        raw = _store.download(path)
        options = json.loads(gzip.decompress(raw))['data']['options']
        import math
        for option in options:
            try:
                gamma = float(option.get('gamma') or 0)
                if math.isfinite(gamma) and gamma != 0:
                    return 'VALIDO'
            except (TypeError, ValueError, OverflowError):
                continue
        return 'NON VALIDO — Gamma assenti'
    except Exception:
        return 'DA VERIFICARE'


def describe_quality(value, store=None):
    """Qualita' senza inferirla da NUOVO/INVARIATO.

    I log recenti contengono i conteggi gamma. Per i log storici, se e' presente
    il file, controlliamo il JSON originale senza modificarlo.
    """
    quality = value.get('quality') or {}
    count = (quality.get('nonzero') or {}).get('gamma')
    if isinstance(count, (int, float)):
        return 'VALIDO' if count > 0 else 'NON VALIDO — Gamma assenti'
    status = str(value.get('status', ''))
    if status == 'DATI_NON_VALIDI':
        return 'NON VALIDO — Gamma assenti'
    path = value.get('file')
    if not path or store is None:
        return 'DA VERIFICARE'
    return _cached_quality_from_file(path, store)


def describe_slot(slot):
    """Non indovinare lo slot di vecchi registri privi di identificazione."""
    name = str(slot or '').strip()
    if name == '30 7 * * 1-5':
        return 'Mattina 07:30'
    if name == '30 13 * * 1-5':
        return '13:30 (studio storico)'
    if name == '30 15 * * 1-5':
        return 'Pomeriggio 15:30 (storico)'
    if name == '46 15 * * 1-5':
        return 'Pomeriggio 15:46'
    if name == '50 15 * * 1-5':
        return 'Pomeriggio · 08:50 Chicago'
    if name == '30 20 * * 1-5':
        return 'Sera · 13:30 Chicago'
    if name == '45 7 * * 1-5':
        return 'Recupero mattina 07:45'
    if name == '45 15 * * 1-5':
        return 'Recupero pomeriggio 15:45'
    if name.lower() in ('manuale', 'manual', 'workflow_dispatch', 'test') or not name:
        return 'Test manuale'
    if name.lower() == 'automatico':
        # Vecchia v1: usava questa etichetta anche nei test avviati a mano.
        return 'Storico — slot non identificato'
    return f'Altro — {name}'


@st.cache_resource
def login_limiter():
    # Protezione BASE per processo, non sostituisce un'autenticazione gestita.
    return {'lock': threading.Lock(), 'failures': {}}


def check_password(entered: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(
        hashlib.sha256(entered.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def week_choice() -> str:
    today = datetime.now(ZoneInfo('Europe/Rome')).date()
    iso = today.isocalendar()
    week = st.text_input('Settimana ISO (AAAA-WNN)', value=f'{iso.year}-W{iso.week:02d}')
    if not re.fullmatch(r'\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])', week):
        st.error('Formato settimana non valido.')
        st.stop()
    return week


st.title('Serafini · SPX — NET GAMMA')


try:
    url = st.secrets['SUPABASE_URL']
    service_key = st.secrets['SUPABASE_SERVICE_ROLE_KEY']
    admin_password = st.secrets['ADMIN_PASSWORD']
    viewer_password = st.secrets['FRIENDS_PASSWORD']
    bucket = st.secrets.get('SUPABASE_BUCKET', 'spx-snapshots')
    if not all([url, service_key, admin_password, viewer_password]):
        raise ValueError('Missing config')
    if hmac.compare_digest(admin_password, viewer_password):
        raise ValueError('Same passwords')
except Exception:
    st.error('Configurazione incompleta: consulta README.md e imposta i Secrets prima dell’uso.')
    st.stop()

if 'role' not in st.session_state:
    st.session_state.role = None

if st.session_state.role not in ('admin', 'friend'):
    st.subheader('Accesso riservato')
    with st.form('auth', clear_on_submit=True):
        password = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Accedi')
    if submitted:
        limiter = login_limiter()
        # Chiave lato server per client session; blocco best-effort, NON IP-based.
        session_id = st.session_state.get('login_id')
        if not session_id:
            import secrets
            session_id = secrets.token_hex(16)
            st.session_state.login_id = session_id
        now = time.monotonic()
        with limiter['lock']:
            count, deadline = limiter['failures'].get(session_id, (0, 0))
            locked = deadline > now
        if locked:
            st.error('Troppi tentativi. Riprova tra qualche minuto.')
        else:
            role = 'admin' if check_password(password, admin_password) else (
                'friend' if check_password(password, viewer_password) else None
            )
            if role:
                with limiter['lock']:
                    limiter['failures'].pop(session_id, None)
                st.session_state.role = role
                st.rerun()
            else:
                with limiter['lock']:
                    count += 1
                    limiter['failures'][session_id] = (count, now + 300 if count >= 5 else 0)
                st.error('Password non corretta.')
    st.stop()

role = st.session_state.role
with st.sidebar:
    st.write('**Accesso:** ' + ('Amministratore' if role == 'admin' else 'Lettore'))
    if st.button('Esci'):
        for key in ('role',):
            st.session_state.pop(key, None)
        st.rerun()
    st.caption('Snapshot SPX archiviati in Supabase Storage privato.')

# La connessione al bucket viene creata SOLO DOPO il controllo password.
store = Store(url, service_key, bucket)

# Contesto operativo unico; il selettore della settimana e i contatori sono sulla stessa riga.
# Apertura iniziale secondo l'ora locale italiana; l'utente può poi aprire/chiudere liberamente.
context_open_initially = datetime.now(ZoneInfo('Europe/Rome')).hour < 13
with st.expander('ANALISI DEL CONTESTO — News · Volatilità · Trend Daily',
                 expanded=context_open_initially):
    col_week, col_snapshots, col_logs = st.columns([2.2, 1, 1], gap='medium', vertical_alignment='bottom')
    with col_week:
        week = week_choice()
    try:
        snaps = store.list(f'{week}/snapshots/')
        logs = store.list(f'{week}/logs/')
    except Exception:
        st.error('Impossibile elencare gli snapshot. Controlla Secrets e bucket Supabase.')
        st.stop()

    # Stessa chiave di selezione usata in gamma_section.render, senza creare
    # un secondo widget Streamlit con la medesima key.
    snapshot_names = sorted((obj['name'] for obj in snaps), reverse=True)
    snapshot_key = f'serafini_selected_snapshot_{week}'
    selected_name = st.session_state.get(snapshot_key)
    if selected_name not in snapshot_names:
        selected_name = snapshot_names[0] if snapshot_names else None
    # La selezione dello snapshot avviene nel modulo NET GAMMA.
    # In caso di refresh/latest, usa lo snapshot più recente anche nel contesto.
    if st.session_state.get(f'spx_refresh_latest_{week}', False) and snapshot_names:
        selected_name = snapshot_names[0]

    context_day = None
    if selected_name:
        try:
            stamp = selected_name.split('SPX_CBOE_', 1)[1][:8]
            context_day = datetime.strptime(stamp, '%Y%m%d').date()
        except (IndexError, ValueError):
            pass
    col_snapshots.metric('Snapshot distinti', len(snaps))
    col_logs.metric('Registri', len(logs))
    st.markdown('#### News e calendario macroeconomico')
    from macro_calendar import render as render_macro_calendar
    render_macro_calendar()
    st.divider()
    from vix_regime import render as render_vix_context
    # One Massive source/session feeds both the volatility card and Trend Daily.
    trend_day = context_day or datetime.now(ZoneInfo('Europe/Rome')).date()
    es_daily_context = None
    daily_error = None
    if context_day is not None:
        try:
            context_data = render_vix_context(context_day, return_context=True)
            es_daily_context = context_data.get('previous_daily_range')
        except Exception:
            st.info('Il quadro VIX non è disponibile per la giornata selezionata.')
            daily_error = 'Quadro giornaliero SPX temporaneamente non disponibile.'
    else:
        st.markdown('#### Andamento e volatilità — seduta precedente (N/D)')
        st.info('Seleziona una settimana con snapshot Cboe validi per analizzare il VIX.')
        # The Trend Daily remains accessible even without a Cboe snapshot.
        try:
            from daily_volatility import previous_es_daily_range
            es_daily_context = previous_es_daily_range(trend_day)
        except Exception:
            daily_error = 'Massive ES non disponibile: verificare Secrets e contratto.'
    st.divider()
    from trend_daily_section import render as render_trend_daily
    es_daily_context = es_daily_context or {}
    render_trend_daily(reference_day=trend_day,
                       daily_bars=es_daily_context.get('daily_bars'),
                       contract=es_daily_context.get('ticker'),
                       daily_error=daily_error)


# Calcolo di sola lettura, anche per il ruolo amici. Nessuna cancellazione qui.
with st.expander('ANALISI NET GAMMA', expanded=False):
    from gamma_section import render
    render(store, week, snaps, logs)

    # Consultazione rilevamenti Cboe disponibile ad amministratore e lettore.
    # Solo la richiesta di un nuovo download rimane riservata all'admin.
    # I registri sono letti solo quando il pannello viene aperto.
    if st.checkbox(f'RILEVAMENTI CBOE — {len(snaps)} snapshot · {len(logs)} registri',
                   value=False, key=f'cb_show_records_{week}'):
        col_snapshots, col_logs = st.columns([1, 1], gap='medium')
        col_snapshots.metric('Snapshot distinti', len(snaps))
        col_logs.metric('Registri', len(logs))

        if logs:
            rows = []
            for obj in logs:
                try:
                    value = json.loads(store.download(f"{week}/logs/{obj['name']}"))
                    rows.append({
                        'Download (ora di Roma)': format_download_rome(value.get('download_utc')),
                        'Acquisizione': (
                            'Test manuale'
                            if str(value.get('scheduled_slot') or '') == '30 13 * * 1-5'
                            and str(value.get('download_rome') or '').startswith('2026-10-22T17:57:')
                            else describe_slot(value.get('scheduled_slot'))
                        ),
                        'Stato': value.get('status', ''),
                        'Qualità dati': describe_quality(value, store),
                        'Timestamp Cboe (originale)': value.get('cboe_timestamp', ''),
                        'SPX': value.get('spot_spx', ''),
                        'Contratti': value.get('contracts', ''),
                        'File originale': value.get('file', ''),
                    })
                except Exception:
                    rows.append({'Stato': 'Impossibile leggere un registro', 'Qualità dati': 'DA VERIFICARE'})
            st.caption('Gli orari di download sono convertiti in Europe/Rome. Il timestamp Cboe rimane come nella fonte, senza attribuirgli un fuso non dichiarato. I vecchi registri etichettati “automatico” non identificano necessariamente lo slot.')
            # La colorazione riguarda solo la qualita', non la colonna Stato (NUOVO/INVARIATO).
            import pandas as pd
            frame = pd.DataFrame(rows)
            def quality_color(series):
                return [
                    'color: #FF4B4B; font-weight: 700' if str(value).startswith('NON VALIDO')
                    else 'color: #28C76F; font-weight: 600' if str(value) == 'VALIDO'
                    else 'color: #EAB308' for value in series
                ]
            st.dataframe(frame.style.apply(quality_color, subset=['Qualità dati']),
                         use_container_width=True, hide_index=True)
        else:
            st.info('Non sono presenti registri per questa settimana.')

        # La consultazione e' per entrambi i ruoli. Solo l'admin puo'
        # richiedere nuove acquisizioni Cboe.
        if role == 'admin':
            from manual_acquisition import render as render_manual_acquisition
            st.divider()
            render_manual_acquisition(role, week, store, embedded=True)


# Edge is a read-only, self-contained comparison panel.
# Never import its calculation engine or feed its values into Serafini.
from edge_triptych import show as show_edge_triptych
show_edge_triptych(week, store, logs)

# Confronto e Replay condividono la stessa sezione, con calcoli invariati.
with st.expander('REPLAY E CONFRONTO', expanded=False):
    mode = st.radio(
        'Modalità di analisi',
        ('Confronto diretto tra due rilevamenti',
         'Replay con candele'),
        index=0,
        key='gamma_analysis_mode',
    )
    if mode == 'Confronto diretto tra due rilevamenti':
        from gamma_section import render_comparison
        render_comparison(store, week, snaps, logs)
    else:
        from gamma_replay import render as render_gamma_replay
        render_gamma_replay(store, week, logs, view_mode='Candele ES + NET GAMMA')

# Report descrittivo e riproducibile per analisi AI (nessuna chiamata a servizi AI).
with st.expander('REPORT GIORNALIERO AI', expanded=False):
    from ai_report import render as render_ai_report
    render_ai_report(store, week, snaps, logs)


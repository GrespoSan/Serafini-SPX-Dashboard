"""Dashboard pubblicamente raggiungibile, dati privati, ruoli admin/lettore.
NON ospitare chiavi nell'app o nel repository: solo Streamlit Secrets.
"""
import gzip
import hashlib
import hmac
import io
import json
import re
import threading
import time
import zipfile
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
        return 'Pomeriggio 15:50'
    if name == '30 20 * * 1-5':
        return 'Sera 20:30'
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


st.title('Serafini · SPX — archivio Cboe')


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
        for key in ('role', 'archive', 'archive_week', 'manifest', 'archive_sha'):
            st.session_state.pop(key, None)
        st.rerun()
    st.caption('Snapshot SPX archiviati in Supabase Storage privato.')

# La connessione al bucket viene creata SOLO DOPO il controllo password.
store = Store(url, service_key, bucket)

# Avviso macro al termine del login, in cima alla dashboard.
from macro_calendar import render as render_macro_calendar
render_macro_calendar()
# Una sola riga: scelta della settimana a sinistra e conteggi a destra.
col_week, col_snapshots, col_logs = st.columns([2.2, 1, 1], gap='medium', vertical_alignment='bottom')
with col_week:
    week = week_choice()

try:
    snaps = store.list(f'{week}/snapshots/')
    logs = store.list(f'{week}/logs/')
except Exception:
    st.error('Impossibile elencare gli snapshot. Controlla Secrets e bucket Supabase.')
    st.stop()

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

# Acquisizione supplementare riservata all'amministratore: gli orari ufficiali restano invariati.
from manual_acquisition import render as render_manual_acquisition
# Centra la sola etichetta dell'expander, mantenendo la freccia di apertura a sinistra.
st.markdown("""
<style>
.st-key-manual_cboe_centered [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] {
    flex: 1;
    text-align: center;
}
.st-key-manual_cboe_centered [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] p {
    text-align: center;
}
</style>
""", unsafe_allow_html=True)
with st.container(key="manual_cboe_centered"):
    render_manual_acquisition(role, week, store)

# Analisi del contesto: modulo Daily indipendente dai motori NET GAMMA/GEX.
with st.expander('TREND DAILY — struttura, range e baricentro', expanded=True):
    from trend_daily_section import render as render_trend_daily
    render_trend_daily()

# Calcolo di sola lettura, anche per il ruolo amici. Nessuna cancellazione qui.
with st.expander('NET GAMMA 0DTE / Weekly', expanded=False):
    from gamma_section import render
    render(store, week, snaps, logs)

# Edge is a read-only, self-contained comparison panel.
# Never import its calculation engine or feed its values into Serafini.
from edge_triptych import show as show_edge_triptych
show_edge_triptych(week, store, logs)

# Confronto e Replay condividono la stessa sezione, con calcoli invariati.
with st.expander('REPLAY e confronto NET GAMMA', expanded=False):
    mode = st.radio(
        'Modalità di analisi',
        ('Replay con candele ES',
         'Replay intraday — evoluzione delle barre',
         'Confronto diretto tra due rilevamenti'),
        index=0,
        key='gamma_analysis_mode',
    )
    if mode == 'Confronto diretto tra due rilevamenti':
        from gamma_section import render_comparison
        render_comparison(store, week, snaps, logs)
    else:
        from gamma_replay import render as render_gamma_replay
        replay_view = ('Candele ES + NET GAMMA'
                       if mode == 'Replay con candele ES' else 'Solo NET GAMMA')
        render_gamma_replay(store, week, logs, view_mode=replay_view)

# Report descrittivo e riproducibile per analisi AI (nessuna chiamata a servizi AI).
with st.expander('REPORT giornaliero per analisi AI — JSON + PDF', expanded=False):
    from ai_report import render as render_ai_report
    render_ai_report(store, week, snaps, logs)

# Archivio settimanale e amministrazione riservati all'amministratore.
if role == 'admin':
    col_zip, col_admin = st.columns([1, 1], gap='medium')
    with col_zip:
        # Il file ZIP non contiene trasformazioni sui dati originali.
        if st.button('Prepara ZIP dati Cboe della settimana', disabled=not (snaps or logs)):
            try:
                with st.spinner('Lettura dei file e creazione ZIP...'):
                    out = io.BytesIO()
                    manifest = []
                    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as archive:
                        for folder, objects in [('snapshots', snaps), ('logs', logs)]:
                            for obj in objects:
                                name = obj['name']
                                path = f'{week}/{folder}/{name}'
                                content = store.download(path)
                                if folder == 'snapshots':
                                    raw = gzip.decompress(content)
                                    payload = json.loads(raw)
                                    if not isinstance(payload.get('data', {}).get('options'), list):
                                        raise ValueError('Formato catena Cboe inatteso')
                                else:
                                    json.loads(content)
                                archive.writestr(path, content)
                                manifest.append({'path': path, 'sha256': hashlib.sha256(content).hexdigest(),
                                                 'bytes': len(content)})
                        archive.writestr('manifest.json', json.dumps({'week': week, 'files': manifest}, indent=2))
                    data = out.getvalue()
                    st.session_state.archive = data
                    st.session_state.archive_week = week
                    st.session_state.manifest = manifest
                    st.session_state.archive_sha = hashlib.sha256(data).hexdigest()
            except Exception:
                st.error('ZIP non generato: un file è illeggibile o la connessione non è riuscita.')

        if st.session_state.get('archive_week') == week and st.session_state.get('archive'):
            sha = st.session_state.archive_sha
            st.success(f"ZIP pronto · {len(st.session_state.archive):,} byte · SHA256: {sha}")
            st.download_button('Scarica ZIP', data=st.session_state.archive,
                               file_name=f'Serafini_SPX_{week}.zip', mime='application/zip')
            st.caption('Verifica sul tuo PC di poter aprire lo ZIP. Scaricare non elimina i file dal cloud.')
    with col_admin:
        with st.expander('Cancella dati Cboe della settimana dal cloud', expanded=False):
            st.warning('Operazione irreversibile. Prima salva e verifica lo ZIP sul tuo PC.')
            if st.session_state.get('archive_week') != week or not st.session_state.get('archive'):
                st.info('Prepara lo ZIP prima di cancellare la settimana.')
            else:
                sha = st.session_state.archive_sha
                typed = st.text_input('Inserisci SHA256 dello ZIP che hai verificato', key=f'sha_{week}')
                confirmed = st.checkbox('Ho scaricato e verificato lo ZIP sul mio PC', key=f'confirmed_{week}')
                ready = confirmed and hmac.compare_digest(typed.strip().lower(), sha)
                if st.button('Elimina settimana da Supabase', disabled=not ready, type='secondary'):
                    exported = {entry['path']: entry['sha256'] for entry in st.session_state.manifest}
                    try:
                        current = [f'{week}/{folder}/{o["name"]}'
                                   for folder in ('snapshots', 'logs') for o in store.list(f'{week}/{folder}/')]
                        if set(current) != set(exported):
                            st.error('Sono arrivati nuovi file dopo lo ZIP: rigenera l’archivio.')
                        elif any(hashlib.sha256(store.download(path)).hexdigest() != exported[path]
                                 for path in current):
                            st.error('Un file cloud è cambiato: rigenera l’archivio.')
                        else:
                            for i in range(0, len(current), 100):
                                store.delete(current[i:i + 100])
                            remaining = [f'{week}/{folder}/{o["name"]}'
                                         for folder in ('snapshots', 'logs') for o in store.list(f'{week}/{folder}/')]
                            if remaining:
                                st.error('Cancellazione parziale: controlla il contenuto del bucket.')
                            else:
                                st.session_state.pop('archive', None)
                                st.success('Settimana eliminata dal cloud. Conserva lo ZIP locale.')
                    except Exception:
                        st.error('Impossibile completare la cancellazione. Controlla il bucket.')

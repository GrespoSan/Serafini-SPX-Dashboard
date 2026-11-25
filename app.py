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

st.set_page_config(page_title='Serafini SPX — Archivio', page_icon='📊', layout='wide')

# Maintenance always runs BEFORE credentials, storage or Massive cache.
from app_maintenance import suspend_if_enabled
suspend_if_enabled(st, "Serafini · SPX — NET GAMMA")

from storage import Store
from spx_massive_day_cache import purge_expired

# Only temporary Massive ES data are purged at the next Rome day. Do not
# clear global st.cache_data: it also holds unrelated immutable Cboe snapshots.
try:
    massive_new_day = purge_expired()
except OSError:
    massive_new_day = False
if massive_new_day:
    from daily_volatility import _fetch_massive_es
    _fetch_massive_es.clear()


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


def describe_slot(slot, rome_date=None):
    """Non indovinare lo slot di vecchi registri privi di identificazione."""
    name = str(slot or '').strip()
    if name == '30 7 * * 1-5':
        return 'Mattina 07:30'
    if name == '30 13 * * 1-5':
        if str(rome_date or '') < '2026-11-11':
            return '13:30 (studio storico)'
        if str(rome_date or '') == '2026-11-11':
            return 'Pre-USA 13:30 · ufficiale storico (11/11)'
        return '13:30 · ufficiale ritirato (dal 12/11)'
    if name == '46 14 * * 1-5':
        return '07:46 Chicago · ufficiale (slot 14:46)'
    if name == '0 17 * * 1-5':
        return 'USA · 10:00 Chicago (17:00 ordinario)'
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
    # Available to BOTH authorized roles. A Streamlit button already triggers
    # a full script rerun, keeping role, selected data and form state intact.
    # Only short-lived/read-only caches are invalidated (no new Cboe download).
    if st.button('🔄 Aggiorna dati', key='spx_sidebar_refresh',
                 use_container_width=True,
                 help='Rilegge le fonti aggiornate senza uscire, cambiare ruolo o richiedere la password.'):
        from spx_refresh import refresh_current_data
        refresh_current_data()
        st.toast('Aggiornamento richiesto: ricarico i dati disponibili.', icon='🔄')
    if st.button('Esci'):
        for key in list(st.session_state.keys()):
            if key in ('role', 'chain_identity', '_ndx_basis_target') or key.startswith(('manual_csv_', 'ndx_', 'gld_', 'uso_', 'universal_', 'v2_', 'pine_download_', 'pine_copy_', 'gamma_candle_', 'gamma_price_chart_')):
                st.session_state.pop(key, None)
        st.rerun()

# Allineamento al layout Multi-Market: CONTEXTO collassato, NET GAMMA aperto,
# selettore archivio SPX nel pannello NET GAMMA.
# Nessun cambiamento ai calcoli o ai flussi Supabase/GitHub.
st.markdown("""
<style>
[data-testid="stMainBlockContainer"],
[data-testid="stAppViewContainer"] .block-container {
    max-width: none !important;
    width: 100% !important;
    box-sizing: border-box;
    padding-left: clamp(1rem, 2vw, 3rem);
    padding-right: clamp(1rem, 2vw, 3rem);
}
.st-key-manual_csv_mercato_selector label p,
.st-key-manual_csv_mercato_selector [data-baseweb="select"] > div,
.st-key-manual_csv_mercato_selector [data-baseweb="select"] span {
    color: #55d5f6 !important;
    font-weight: 700 !important;
}
[class*="st-key-pine_download_"] button,
[class*="st-key-pine_download_"] button p,
[class*="st-key-pine_download_"] button span,
[class*="st-key-pine_copy_"] [data-testid="stExpander"] summary,
[class*="st-key-pine_copy_"] [data-testid="stExpander"] summary p,
[class*="st-key-pine_copy_"] [data-testid="stExpander"] summary span {
    color: #ff626b !important;
    font-weight: 750 !important;
}
.st-key-manual_csv_pine_download_control button,
.st-key-manual_csv_pine_download_control button p,
.st-key-manual_csv_pine_copy_container [data-testid="stExpander"] summary,
.st-key-manual_csv_pine_copy_container [data-testid="stExpander"] summary p {
    color: #ff626b !important;
    font-weight: 750 !important;
}
</style>
""", unsafe_allow_html=True)

with st.expander('ANALISI NET GAMMA', expanded=False):
    st.header('NET GAMMA · formula Serafini')
    st.subheader('Fotografie opzioni SPX · Cboe storico / Alpaca nuovo')
    snapshot_slot = st.container()
    storage_unavailable = False
    store = Store(url, service_key, bucket)

    # SPX-specific archive controls stay in NET GAMMA, not in the shared context.
    col_week, col_snapshots, col_logs = st.columns([2.2, 1, 1], gap='medium', vertical_alignment='bottom')
    with col_week:
        week = week_choice()
    try:
        snaps = store.list(f'{week}/snapshots/')
        logs = store.list(f'{week}/logs/')
    except Exception:
        storage_unavailable = True
        snaps, logs = [], []
    col_snapshots.metric('Snapshot distinti', len(snaps))
    col_logs.metric('Registri', len(logs))

    with snapshot_slot:
        from gamma_section import render_snapshot_selector
        render_snapshot_selector(store, week, snaps, logs)
    from gamma_section import render
    asia_panel = render(store, week, snaps, logs, show_heading=False, selector_external=True)
    # Nessun controllo periodico di Supabase: il selettore segue l'ultimo
    # download solo all'apertura/ricaricamento o durante una normale interazione.
    # Consultazione rilevamenti Cboe disponibile ad amministratore e lettore.
    # Solo la richiesta di un nuovo download rimane riservata all'admin.
    # I registri sono letti solo quando il pannello viene aperto.
    # Con file locale non esistono registri Supabase: evita il riquadro
    # «1 snapshot · 0 registri» in fondo ai grafici e ai quattro export Pine.
    # Mantieni la consultazione storica per fotografie archiviate e log reali.
    if logs and st.checkbox(
            f'RILEVAMENTI SPX — {len(snaps)} snapshot · {len(logs)} registri',
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
                            else ('13:30 (studio storico)'
                                  if str(value.get('scheduled_slot') or '') == '30 13 * * 1-5'
                                  and str(value.get('rome_date') or '') < '2026-11-11'
                                  else describe_slot(value.get('scheduled_slot'), value.get('rome_date')))
                        ),
                        'Stato': value.get('status', ''),
                        'Qualità dati': describe_quality(value, store),
                        'Timestamp sorgente': value.get('source_timestamp_utc') or value.get('cboe_timestamp', ''),
                        'Fonte': value.get('source') or 'CBOE_ORIGINALE',
                        'SPX': value.get('spot_spx', ''),
                        'Contratti': value.get('contracts', ''),
                        'File sorgente': value.get('file', ''),
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
                         width="stretch", hide_index=True)
        else:
            st.info('Non sono presenti registri per questa settimana.')

        # I registri restano consultabili solo quando l'archivio è disponibile.

# Manual CSV is deliberately independent from automatic SPX archive state.
try:
    from manual_csv.panel import render as render_manual_csv_net_gamma
    render_manual_csv_net_gamma(mt4_store=store if role == 'admin' else None)
except Exception as exc:
    st.warning("Analisi NET GAMMA CSV non disponibile: " + type(exc).__name__)

# Shared CSV GEX mathematics and presentation. Selected NET GAMMA source
# is the only input; retired Edge comparison no longer runs.
with st.expander('ANALISI GEX · Edge-style — modulo indipendente', expanded=False):
    try:
        from spx_csv_gex_section import render as show_csv_gex
        show_csv_gex(week, store)
    except Exception as exc:
        st.warning('GEX non disponibile: '+type(exc).__name__+
                   '. NET GAMMA rimane utilizzabile; nessun livello sostitutivo.')
    
# Same position as Serafini CSV: context follows GEX; populate last to avoid delaying other panels.
context_slot = st.empty()

# Confronto e Replay condividono la stessa sezione, con calcoli invariati.
if not storage_unavailable:
    with st.expander('REPLAY E CONFRONTO', expanded=False):
        mode = st.radio(
            'Modalità di analisi',
            ('Replay con candele',
             'Confronto diretto tra due rilevamenti'),
            index=0,
            key='gamma_analysis_mode_v2',
        )
        if mode == 'Confronto diretto tra due rilevamenti':
            from gamma_section import render_comparison
            render_comparison(store, week, snaps, logs)
        else:
            from gamma_replay import render as render_gamma_replay
            render_gamma_replay(store, week, logs, view_mode='Candele ES + NET GAMMA')

# Source of truth: Serafini-CSV/app.py · ANALISI DEL CONTESTO.
# Only the archive week label and verified morning-data adapter are SPX-specific.
with context_slot.container():
    with st.expander("ANALISI DEL CONTESTO — News · Volatilità · Trend Daily", expanded=False):
        week_label = week
        st.markdown(f"**Settimana ISO (AAAA-WNN)** · {week_label}")
        # L'analisi viene visualizzata automaticamente; il download Massive ES
        # viene gestito dalla cache condivisa giornaliera. Anche con expander
        # chiuso il codice esegue, ma i dati Massive già letti non si riscaricano.
        st.markdown('#### News e calendario macroeconomico')
        from macro_calendar import render as render_macro_calendar
        render_macro_calendar()
        st.divider()
    
        # Il quadro VX e il Daily usano le proprie fonti CBOE/Massive.
        # La data corrente italiana non dipende dalla presenza di snapshot
        # Supabase, dal numero di registri o dal file CBOE selezionato.
        from vix_regime import render as render_vix_context
        trend_day = datetime.now(ZoneInfo('Europe/Rome')).date()
        es_daily_context = None
        daily_error = None
        try:
            context_data = render_vix_context(trend_day, return_context=True)
            es_daily_context = context_data.get('previous_daily_range')
        except Exception:
            st.info('Il quadro volatilità VX non è disponibile per la giornata corrente.')
            # Il Trend Daily conserva la sua fonte Massive anche se la
            # lettura dei settlement CBOE fallisce.
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
                           daily_error=daily_error,
                           modeled_gex=None)
    
        # Il Quadro è sempre visibile dopo il Trend Daily. Senza uno snapshot SPX
        # resta la lettura autonoma dell'Asia ES, senza inventare candidati C1/P1.
        st.divider()
        st.subheader("QUADRO OPERATIVO — ORE 09:00")
        from asia_gamma import render_auto as render_asia_gamma
        if asia_panel is not None:
            render_asia_gamma(
                asia_panel['day'], asia_panel['c1_es'], asia_panel['p1_es'],
                level_context=asia_panel['level_context'],
            )
        else:
            render_asia_gamma(trend_day, None, None)

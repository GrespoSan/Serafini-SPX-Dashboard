"""Acquisizione manuale Cboe con attesa non bloccante del registro GitHub/Supabase.

La richiesta workflow_dispatch non sostituisce le acquisizioni programmate.
Il pannello di stato effettua polling soltanto dopo una richiesta e non richiede
ricaricare il browser (quindi mantiene la sessione Streamlit autenticata).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import re
import threading
import time

import streamlit as st

POLL_SECONDS = 5
MAX_WAIT_SECONDS = 150


def dispatch_snapshot(token: str, repo: str, workflow: str = 'spx.yml') -> None:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Nome repository GitHub non valido.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml', workflow):
        raise ValueError('Nome workflow non valido.')
    request = Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches',
        data=json.dumps({'ref': 'main'}).encode('utf-8'),
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'serafini-spx-dashboard',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 204:
                raise RuntimeError(f'GitHub HTTP {response.status}, atteso 204.')
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError('Autorizzazione GitHub insufficiente o token scaduto.') from None
        if exc.code == 404:
            raise RuntimeError('Repository o workflow GitHub non trovato.') from None
        raise RuntimeError(f'Richiesta GitHub non accettata (HTTP {exc.code}).') from None
    except URLError:
        raise RuntimeError('Connessione a GitHub non disponibile.') from None


@st.cache_resource
def _global_request_limit():
    return {'lock': threading.Lock(), 'last': 0.0}


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def find_manual_result(store, week: str, requested_utc: datetime):
    """Legge SOLO i log manuali successivi alla richiesta, anche se lo stato e' errore.

    Non confonde la disponibilita' del log con quella dello snapshot: un NUOVO
    viene validato verificando che il file registrato esista in Supabase.
    """
    recent = []
    threshold = requested_utc - timedelta(seconds=3)
    for item in store.list(f'{week}/logs/'):
        try:
            raw = store.download(f"{week}/logs/{item['name']}")
            log = json.loads(raw)
        except (ValueError, KeyError, OSError, RuntimeError, TypeError):
            continue
        if log.get('sessione') != 'manuale' or log.get('tipo_tentativo') != 'manuale':
            continue
        timestamp = _parse_utc(log.get('download_utc'))
        if timestamp is None or timestamp < threshold:
            continue
        recent.append((timestamp, log))
    if not recent:
        return None
    _, result = max(recent, key=lambda pair: pair[0])
    if result.get('status') == 'NUOVO':
        saved = result.get('file', '')
        # Il log NUOVO viene scritto dopo il salvataggio dello snapshot.
        if not saved or saved.rsplit('/', 1)[-1] not in {
            x['name'] for x in store.list(f'{week}/snapshots/')
        }:
            return None
    return result


@st.fragment(run_every=f'{POLL_SECONDS}s')
def _pending_monitor(store, week: str):
    pending = st.session_state.get('manual_spx_pending')
    if not pending:
        return
    requested = _parse_utc(pending.get('requested_utc'))
    if requested is None or pending.get('week') != week:
        st.session_state.pop('manual_spx_pending', None)
        return
    elapsed = time.monotonic() - pending['started_monotonic']
    try:
        result = find_manual_result(store, week, requested)
    except Exception:
        result = None
    if result is not None:
        status = result.get('status', '')
        st.session_state['manual_spx_outcome'] = {
            'status': status,
            'error': str(result.get('error', ''))[:180],
            'file': result.get('file', ''),
        }
        st.session_state.pop('manual_spx_pending', None)
        st.rerun()  # Streamlit rerun della app, NON refresh del browser; login resta attivo.
    if elapsed >= MAX_WAIT_SECONDS:
        st.session_state['manual_spx_outcome'] = {'status': 'ATTESA_SCADUTA'}
        st.session_state.pop('manual_spx_pending', None)
        st.rerun()
    st.info(f'Acquisizione richiesta: controllo automatico in corso ({int(elapsed)} s). '
            'Non aggiornare la pagina. Attendo il risultato da GitHub/Supabase…')


def render(role: str, week: str, store) -> None:
    if role != 'admin':
        return
    with st.expander('Acquisizione Cboe supplementare', expanded=False):
        st.caption('Nuova acquisizione manuale. I rilevamenti automatici delle 07:30 e 15:46 restano invariati.')
        rome = datetime.now(ZoneInfo('Europe/Rome'))
        iso = rome.isocalendar()
        if rome.weekday() >= 5:
            st.info('Il downloader non acquisisce dati nel fine settimana.')
            return
        if week != f'{iso.year}-W{iso.week:02d}':
            st.warning('Seleziona la settimana corrente per avviare una nuova acquisizione.')
            return
        token = st.secrets.get('GITHUB_DISPATCH_TOKEN', '')
        repo = st.secrets.get('GITHUB_CLOUD_REPOSITORY', 'GrespoSan/Serafini-SPX-Cloud')
        if not token:
            st.info('Configura GITHUB_DISPATCH_TOKEN nei Secrets di Streamlit.')
            return

        pending = st.session_state.get('manual_spx_pending')
        limiter = _global_request_limit()
        with limiter['lock']:
            wait = max(0, int(60 - (time.monotonic() - limiter['last']))) if limiter['last'] else 0
        if st.button('Scarica nuovo snapshot Cboe', key='spx_manual_dispatch',
                     disabled=bool(pending) or wait > 0, use_container_width=True):
            try:
                with limiter['lock']:
                    if limiter['last'] and time.monotonic() - limiter['last'] < 60:
                        raise RuntimeError('È già stata inviata una richiesta. Attendi un minuto.')
                    limiter['last'] = time.monotonic()
                requested = datetime.now(timezone.utc)
                with st.spinner('Richiesta di acquisizione a GitHub...'):
                    dispatch_snapshot(token, repo)
                st.session_state['manual_spx_pending'] = {
                    'week': week,
                    'requested_utc': requested.isoformat(),
                    'started_monotonic': time.monotonic(),
                }
                st.session_state.pop('manual_spx_outcome', None)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        if wait and not pending:
            st.caption(f'Nuova richiesta disponibile tra circa {wait} secondi.')
        if pending:
            _pending_monitor(store, week)
        outcome = st.session_state.get('manual_spx_outcome')
        if outcome and not pending:
            status = outcome['status']
            if status == 'NUOVO':
                st.success('Nuovo snapshot salvato. Elenco aggiornato automaticamente; puoi confrontarlo subito.')
            elif status == 'INVARIATO':
                st.info('Acquisizione completata: Cboe ha restituito uno snapshot già presente.')
            elif status in ('DATI_NON_VALIDI', 'ERRORE'):
                st.warning('Nuovi dati non utilizzabili: ' +
                           (outcome.get('error') or status) +
                           '. L’archivio degli snapshot validi resta invariato.')
            elif status == 'ATTESA_SCADUTA':
                st.warning('Non è arrivata conferma entro circa 2 minuti e mezzo. '
                           'Controlla GitHub Actions o riprova più tardi; non è confermato un nuovo download.')
            else:
                st.info(f'Esito acquisizione: {status}.')
        st.caption('Quando GitHub salva il risultato, la dashboard aggiorna l’elenco senza F5. '
                   'Gli snapshot manuali non sostituiscono quelli ufficiali.')

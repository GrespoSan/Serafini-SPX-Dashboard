"""Richiesta autenticata di snapshot Cboe supplementare via GitHub Actions.

Non acquisisce direttamente dati; non modifica schedule automatico o Supabase.
La conferma GitHub 204 significa richiesta accettata, NON snapshot salvato.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import re
import threading
import time
import streamlit as st


def dispatch_snapshot(token: str, repo: str, workflow: str = 'spx.yml') -> None:
    """Innesca workflow_dispatch senza input: SLOT risulta vuoto, quindi manuale.

    GitHub Actions: write sul repository del downloader.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Nome repository non valido: atteso proprietario/repository')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml', workflow):
        raise ValueError('Nome workflow non valido')
    url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches'
    request = Request(url,
        data=json.dumps({'ref': 'main'}).encode('utf-8'),
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'serafini-spx-dashboard',
            'Content-Type': 'application/json',
        })
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 204:
                raise RuntimeError(f'GitHub ha risposto HTTP {response.status}, atteso 204')
    except HTTPError as exc:
        # Non stampare corpo o headers: potrebbero rivelare dati riservati.
        if exc.code in (401, 403):
            raise RuntimeError(f'Autorizzazione GitHub insufficiente (HTTP {exc.code}). Controlla Actions: write e la scadenza del token.') from None
        if exc.code == 404:
            raise RuntimeError('Workflow o repository GitHub non trovato, oppure token privo di accesso.') from None
        if exc.code == 422:
            raise RuntimeError('Workflow non attivo oppure ramo main non valido (HTTP 422).') from None
        raise RuntimeError(f'GitHub non ha accettato la richiesta (HTTP {exc.code}).') from None
    except URLError:
        raise RuntimeError('Connessione a GitHub non disponibile.') from None


@st.cache_resource
def _global_request_limit():
    """Un solo trigger ogni 60 secondi per processo, anche fra utenti differenti."""
    return {'lock': threading.Lock(), 'last': 0.0}


def render(role: str, week: str) -> None:
    """Disponibile dopo login a entrambi i ruoli; token esclusivamente server-side."""
    if role not in ('admin', 'friend'):
        return
    with st.expander('Acquisizione Cboe supplementare', expanded=False):
        st.caption('Disponibile agli utenti con accesso alla dashboard. I rilievi automatici (07:30 e 15:46) rimangono invariati. '
                   'Il nuovo download è un rilevamento MANUALE e non sostituisce gli altri.')
        rome = datetime.now(ZoneInfo('Europe/Rome'))
        iso = rome.isocalendar()
        current_week = f'{iso.year}-W{iso.week:02d}'
        if rome.weekday() >= 5:
            st.info('Il downloader non acquisisce dati il sabato o la domenica.')
            return
        if week != current_week:
            st.warning('Seleziona la settimana corrente per eseguire un nuovo download.')
            return
        token = st.secrets.get('GITHUB_DISPATCH_TOKEN', '')
        repo = st.secrets.get('GITHUB_CLOUD_REPOSITORY', 'GrespoSan/Serafini-SPX-Cloud')
        if not token:
            st.info('Per attivare il pulsante, configura GITHUB_DISPATCH_TOKEN nei Secrets di Streamlit.')
            return
        limiter = _global_request_limit()
        with limiter['lock']:
            last = limiter['last']
            wait = max(0, int(60 - (time.monotonic() - last))) if last else 0
        action_col, refresh_col = st.columns([1, 1])
        with action_col:
            requested = st.button('Scarica nuovo snapshot Cboe', key='spx_manual_dispatch',
                                  disabled=wait > 0, use_container_width=True)
        with refresh_col:
            if st.button('Aggiorna elenco snapshot', key='spx_manual_refresh', use_container_width=True):
                st.rerun()
        if wait:
            st.caption(f'Richiesta appena inviata; attendi circa {wait} secondi prima di richiederne un’altra.')
        if requested:
            try:
                # Il limite vale anche se due utenti cliccano contemporaneamente.
                with limiter['lock']:
                    elapsed = time.monotonic() - limiter['last']
                    if limiter['last'] and elapsed < 60:
                        raise RuntimeError('È già stata richiesta un’acquisizione di recente. Attendi un minuto.')
                    # Riserva il tempo prima della chiamata esterna; anche una risposta lenta
                    # non permette l'invio simultaneo di altre richieste.
                    limiter['last'] = time.monotonic()
                with st.spinner('Invio richiesta a GitHub Actions...'):
                    dispatch_snapshot(token, repo)
                st.session_state['manual_spx_request_time'] = time.monotonic()
                st.success('Richiesta accettata da GitHub. Il download NON è ancora confermato. '
                           'Attendi l’esecuzione e poi premi «Aggiorna elenco snapshot».')
            except Exception as exc:
                st.error(str(exc))
        st.caption('Per confrontare, apri «Confronto NET GAMMA», seleziona lo snapshot delle 15:46 '
                   'come iniziale e quello manuale come finale. Se Cboe restituisce lo stesso timestamp, '
                   'il downloader registra INVARIATO e non duplica il file.')

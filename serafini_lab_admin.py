"""SERAFINI LAB: server-side ADMIN-only, read-only private archive viewer.

No public Github raw URL, no client exposure of credentials, no changes to
Serafini NET GAMMA or GEX. Never caches/prints private report payload for
unauthenticated viewers. Optional read-only fine-grained token configured
as SERAFINI_LAB_GITHUB_TOKEN in Streamlit server secrets.
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = "research/higher_greeks_reports/daily"
REPO = "GrespoSan/Serafini-SPX-Cloud"
MAX_BYTES = 180_000
DAILY_SCHEMA = "serafini_lab_daily_v1"
MODELS = ("H0_carry", "H1_color_only", "H2_nonlinear_decay")
LABELS = {
    "H0_carry": "Gamma invariato",
    "H1_color_only": "Solo Color",
    "H2_nonlinear_decay": "Ricalcolo temporale",
}


def require_admin(role):
    """Raise before any I/O or UI for a friend or missing session role."""
    if role != "admin":
        raise PermissionError("SERAFINI LAB riservato all'amministratore")


def report_path(day: date) -> str:
    if not isinstance(day, date):
        raise ValueError("Invalid report date")
    return f"{ROOT}/{day.year}/Serafini_LAB_{day:%Y%m%d}.json"


def _validate_report(data, requested_day):
    if not isinstance(data, dict) or data.get("schema") != DAILY_SCHEMA:
        raise ValueError("Schema LAB inatteso")
    if data.get("date") != requested_day.isoformat():
        raise ValueError("Data LAB differente dalla richiesta")
    if data.get("status") not in ("DIAGNOSTIC_ONLY", "WAIT"):
        raise ValueError("Stato LAB inatteso")
    if data["status"] == "DIAGNOSTIC_ONLY":
        score = data.get("score")
        if not isinstance(score, dict) or score.get("status") != "HISTORICAL_SCORE_NOT_EDGE":
            raise ValueError("Score LAB mancante o inatteso")
        models = score.get("models")
        if not isinstance(models, dict) or any(model not in models for model in MODELS):
            raise ValueError("Risultati modelli mancanti")
    return data


def fetch_private_report(day, token, role, opener=None):
    """Download via authenticated server request; no public fallback.

    404 means no immutable report for date; other failures are explicit.
    No arbitrary URL accepted, only fixed private repo/path/date.
    """
    require_admin(role)
    if not isinstance(token, str) or not token.strip():
        return None, "NON_COLLEGATO"
    if len(token) > 1024 or "\n" in token or "\r" in token:
        raise ValueError("Credenziale non valida")
    url = f"https://api.github.com/repos/{REPO}/contents/{report_path(day)}?ref=main"
    request = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Serafini-LAB-admin-viewer",
    }, method="GET")
    opener = opener or urlopen
    try:
        with opener(request, timeout=8) as response:
            blob = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None, "NESSUN_REPORT"
        if exc.code in (401, 403):
            return None, "ACCESSO_ARCHIVIO_NEGATO"
        return None, "ARCHIVIO_NON_DISPONIBILE"
    except (URLError, TimeoutError, OSError):
        return None, "ARCHIVIO_NON_DISPONIBILE"
    if len(blob) > MAX_BYTES:
        raise ValueError("Report LAB troppo grande")
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if not isinstance(envelope, dict) or envelope.get("type") != "file" or envelope.get("encoding") != "base64":
            raise ValueError("Risposta archivio LAB inattesa")
        raw = base64.b64decode(envelope["content"], validate=False)
        if len(raw) > MAX_BYTES:
            raise ValueError("Report LAB troppo grande")
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, base64.binascii.Error) as exc:
        raise ValueError("Report LAB non valido") from exc
    return _validate_report(parsed, day), "DISPONIBILE"


def render(role):
    require_admin(role)
    import streamlit as st

    with st.expander("SERAFINI LAB — GRECHE SUPERIORI", expanded=False):
        st.caption("Solo ADMIN · Ricerca diagnostica in sola lettura · "
                   "NET GAMMA Serafini invariato")
        today = datetime.now().date()
        day = st.date_input("Giornata di ricerca", value=today,
                            max_value=today, key="serafini_lab_day")
        st.caption("Il laboratorio confronta H0 Gamma invariato, H1 Color "
                   "e H2 ricalcolo temporale. Non sono segnali LONG/SHORT.")
        token = st.secrets.get("SERAFINI_LAB_GITHUB_TOKEN", "")
        if not token:
            st.info("Archivio LAB non collegato al Dashboard. "
                    "Il calcolo automatico risiede nel repository Cloud privato. "
                    "Per consultare qui i rapporti serve una credenziale server-side "
                    "di sola lettura; nessun dato viene mostrato ai lettori.")
            return
        try:
            report, state = fetch_private_report(day, token, role)
        except ValueError:
            st.warning("Rapporto LAB non leggibile: formato o contenuto non valido.")
            return
        if report is None:
            messages = {
                "NESSUN_REPORT": "Nessun rapporto definitivo per questa data. "
                                 "Può essere in attesa degli snapshot o dei controlli qualità.",
                "ACCESSO_ARCHIVIO_NEGATO": "Accesso all'archivio LAB non autorizzato. "
                                            "Verificare il token server-side.",
                "ARCHIVIO_NON_DISPONIBILE": "Archivio LAB temporaneamente non disponibile.",
                "NON_COLLEGATO": "Archivio LAB non collegato.",
            }
            st.info(messages.get(state, "Nessun dato LAB disponibile."))
            return
        if report["status"] == "WAIT":
            st.warning("WAIT — " + str(report.get("reason") or "Dati insufficienti"))
            return
        score = report["score"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Contratti valutati", str(score.get("scored", "N/D")))
        c2.metric("Fotografie richieste", "3")
        c3.metric("Stato", "Diagnostico")
        rows = []
        for model in MODELS:
            values = score["models"][model]
            rows.append({
                "Modello": LABELS[model],
                "MAE Gamma": values.get("mae_gamma"),
                "MAE ponderata": values.get("weighted_mae_gamma"),
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Valutazione retrospettiva con input T1; non è validazione "
                   "indipendente né previsione del prezzo. Nessuna posizione "
                   "dealer osservata.")
        with st.expander("Qualità, provenienza e limitazioni", expanded=False):
            st.json({
                "data": report.get("date"),
                "chiusura_precedente": report.get("reference_previous_close"),
                "intervallo_ore": report.get("planned_horizon_hours"),
                "parita_spot_opzioni": report.get("spot_option_parity"),
                "fonti": report.get("source_provenance"),
                "limitazioni": report.get("limitations", []),
            }, expanded=False)

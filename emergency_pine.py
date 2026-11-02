"""Pine di emergenza SPX: una sola fotografia CBOE, senza Supabase.

Riutilizza esattamente i motori Net Gamma, estensione e Pine ufficiali.
Niente cron, Replay, upload remoto o confronto tra orari.
"""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import gzip
import math
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import streamlit as st

from es_spx_basis import download_and_calculate
from net_gamma import profile, read_snapshot, visual_profile
from offline_snapshot import InvalidLocalSnapshot, load_local_snapshot
from pine_export import make_pine
from volatility_levels import weekly_expiry

ROME = ZoneInfo("Europe/Rome")
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"
MAX_RAW = 90_000_000
STORE_KEY = "emergency_pine_snapshot"
BASIS_PREFIX = "emergency_pine_basis_"


def download_current_snapshot():
    """Scarica SOLO dopo click. Memorizza in RAM una copia gzip non modificata."""
    request = Request(CBOE_URL, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=60) as response:
        raw = response.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW:
        raise InvalidLocalSnapshot("Risposta CBOE oltre il limite consentito.")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    store = load_local_snapshot("SPX_CBOE_corrente.json.gz", compressed)
    return {"name": "SPX_CBOE_corrente.json.gz", "content": compressed,
            "source": "Download manuale CBOE",
            "download_rome": datetime.now(ROME).isoformat(timespec="seconds"),
            "timestamp": store.timestamp}


def use_uploaded_snapshot(filename, payload):
    """Conserva in memoria i byte originali, dopo la validazione esistente."""
    store = load_local_snapshot(filename, payload)
    return {"name": filename, "content": bytes(payload),
            "source": "Importazione locale",
            "download_rome": None,
            "timestamp": store.timestamp}


def calculate_current_profiles(snapshot, day: date):
    """I 50 strike Serafini sono immutati; estensione solo grafica/Pine."""
    if day.weekday() >= 5:
        raise ValueError("Non ci sono 0DTE SPX feriali in questa data.")
    reference = float(snapshot["data"]["prev_day_close"])
    spot = float(snapshot["data"]["current_price"])
    if not (math.isfinite(reference) and reference > 0 and
            math.isfinite(spot) and spot > 0):
        raise ValueError("Riferimento SPX o spot CBOE non valido.")
    expiries = {"0DTE": day, "Weekly": weekly_expiry(day)}
    profiles = {}
    for kind, expiry in expiries.items():
        core = profile(snapshot, expiry, reference)
        if len(core["rows"]) != 50:
            raise ValueError(f"{kind}: servono 25 strike sotto e 25 sopra.")
        if not any(float(row["net_gamma"]) != 0 for row in core["rows"]):
            raise ValueError(f"{kind}: NET GAMMA nullo, dati non utilizzabili.")
        visual = visual_profile(snapshot, core, expiry, reference, spot)
        profiles[kind] = {
            **core,
            "display_rows": visual["rows"],
            "visual_coverage_complete": visual["coverage_complete"],
        }
    return profiles, reference, spot


def generate_current_pine(snapshot, day: date, basis, cboe_stamp, label):
    """Un Pine soltanto: senza precedenti, stesso make_pine della dashboard."""
    value = float(basis)
    if not math.isfinite(value):
        raise ValueError("Differenziale ES-SPX non finito.")
    profiles, reference, spot = calculate_current_profiles(snapshot, day)
    script = make_pine(
        profiles["0DTE"], profiles["Weekly"], value,
        day.isoformat(), cboe_stamp,
        acquisition_label=label, current_only=True,
    )
    return script, profiles, reference, spot


def _auto_basis(day: date, key: str):
    result = download_and_calculate(day)
    suggested = int(Decimal(str(result["basis"])).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    st.session_state[key] = str(suggested)
    st.session_state[f"{key}_details"] = {
        "raw": float(result["basis"]),
        "rounded": suggested,
        "day": str(result["reference_date"]),
        "bars": int(result["bar_count"]),
        "source": result["source"],
    }
    st.session_state.pop(f"{key}_error", None)


def _recalculate_basis(day: date, key: str):
    try:
        _auto_basis(day, key)
    except Exception as exc:
        # Non cancellare l'eventuale valore digitato a mano.
        st.session_state[f"{key}_error"] = (
            f"{type(exc).__name__}: {str(exc)[:180]}")


def _chart(rows, title, offset):
    import plotly.graph_objects as go

    prices = [float(item["strike_spx"]) + offset for item in rows]
    gammas = [float(item["net_gamma"]) for item in rows]
    chart = go.Figure(go.Bar(
        x=gammas, y=prices, orientation="h",
        marker_color=["#22c55e" if gamma >= 0 else "#ef4444"
                      for gamma in gammas],
        hovertemplate="Livello ES %{y:,.2f}<br>NET GAMMA %{x:+.3f}<extra></extra>",
    ))
    chart.add_vline(x=0, line_color="#9ca3af", line_width=1)
    chart.update_layout(
        title=title, template="plotly_dark",
        height=680, margin=dict(l=20, r=20, t=55, b=50),
        xaxis_title="NET GAMMA Serafini", yaxis_title="Prezzo ES stimato",
        showlegend=False,
    )
    return chart


def render():
    st.caption(
        "Rilevamento singolo: download CBOE richiesto manualmente o importazione "
        "ZIP/JSON. Nessun Supabase, nessun confronto storico, nessun salvataggio remoto."
    )
    left, right = st.columns(2, gap="medium")
    with left:
        if st.button("Scarica CBOE adesso", key="emergency_pine_download",
                     use_container_width=True):
            st.session_state.pop(STORE_KEY, None)
            try:
                with st.spinner("Scaricamento diretto CBOE (solo manuale)..."):
                    st.session_state[STORE_KEY] = download_current_snapshot()
            except Exception as exc:
                st.error("Download CBOE non riuscito: " +
                         f"{type(exc).__name__}: {str(exc)[:180]}")
    with right:
        uploaded = st.file_uploader(
            "In alternativa importa ZIP / JSON / JSON.GZ",
            type=["zip", "json", "gz"],
            key="emergency_pine_uploader",
        )
        if st.button("Usa file importato", key="emergency_pine_import",
                     disabled=uploaded is None, use_container_width=True):
            st.session_state.pop(STORE_KEY, None)
            try:
                st.session_state[STORE_KEY] = use_uploaded_snapshot(
                    uploaded.name, uploaded.getvalue())
            except (InvalidLocalSnapshot, ValueError) as exc:
                st.error(f"Importazione non valida: {exc}")

    active = st.session_state.get(STORE_KEY)
    if not active:
        st.info("Scarica una fotografia CBOE oppure importa un file per calcolare il Pine corrente.")
        return
    try:
        local = load_local_snapshot(active["name"], active["content"])
        snapshot = read_snapshot(local.download(local.object_path))
        day = date.fromisoformat(local.timestamp[:10])
    except (InvalidLocalSnapshot, ValueError, KeyError, TypeError, OSError) as exc:
        st.error(f"Fotografia CBOE non utilizzabile: {exc}")
        return

    st.markdown(f"**Fonte:** {active['source']} · **Data CBOE:** {day:%d/%m/%Y} "
                f"· **timestamp originale:** {local.timestamp} (fuso non dichiarato)")
    if active["download_rome"]:
        st.caption("Download effettuato alle " + active["download_rome"][11:19] +
                   " ora di Roma; il timestamp CBOE non identifica quell'ora.")
    else:
        st.caption("File importato: l'ora effettiva del download originario non è verificabile.")
    if day != datetime.now(ROME).date():
        st.warning("Il file non è datato oggi (ora di Roma): "
                   "per evitare un Pine presentato come corrente, acquisisci "
                   "una fotografia della giornata.")
        return
    if day.weekday() >= 5:
        st.warning("Per questa data non esiste un 0DTE SPX feriale valido.")
        return

    basis_key = BASIS_PREFIX + day.isoformat()
    attempted = basis_key + "_attempted"
    if attempted not in st.session_state:
        st.session_state[attempted] = True
        if not str(st.session_state.get(basis_key, "")).strip():
            _recalculate_basis(day, basis_key)
    st.markdown("**Differenziale ES − SPX (chiusure M15 cash precedente)**")
    b1, b2 = st.columns([2.5, 1], gap="small")
    with b1:
        text_basis = st.text_input(
            "Differenziale automatico, correggibile manualmente",
            key=basis_key, placeholder="Esempio: 48",
            help="Metodo esistente Yahoo 15m ES=F − ^GSPC, arrotondato "
                 "all'intero. Verifica la corrispondenza con il future ES su TradingView.",
        )
    with b2:
        st.markdown('<div style="height:29px"></div>', unsafe_allow_html=True)
        st.button("Ricalcola", key="emergency_pine_basis_refresh",
                  on_click=_recalculate_basis, args=(day, basis_key),
                  use_container_width=True)
    details = st.session_state.get(f"{basis_key}_details")
    if details:
        st.caption(
            f"Calcolo automatico: {details['raw']:.2f} → {details['rounded']} punti "
            f"· sessione {details['day']} · {details['bars']} barre "
            f"· fonte: {details['source']}."
        )
    if st.session_state.get(f"{basis_key}_error"):
        st.caption("Calcolo automatico non disponibile: " +
                   st.session_state[f"{basis_key}_error"] +
                   ". Inserisci il differenziale verificato manualmente.")
    if not text_basis.strip():
        st.info("Inserisci un differenziale ES−SPX verificato per generare il Pine ES/MES.")
        return
    try:
        basis = float(text_basis.strip().replace(",", "."))
        if not math.isfinite(basis):
            raise ValueError("Valore non finito")
    except ValueError:
        st.error("Differenziale ES−SPX non numerico.")
        return
    label = ("Download manuale " + active["download_rome"][:16].replace("T", " ") + " Roma"
             if active["download_rome"] else "Importazione locale - ora download ignota")
    try:
        pine, profiles, reference, spot = generate_current_pine(
            snapshot, day, basis, local.timestamp, label)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        st.error(f"NET GAMMA o Pine non generabile: {exc}")
        return

    st.caption(f"Chiusura precedente SPX {reference:,.2f} · spot CBOE {spot:,.2f} "
               f"· differenziale ES−SPX applicato {basis:+.2f} · "
               f"0DTE {profiles['0DTE']['expiry']} · Weekly {profiles['Weekly']['expiry']}.")
    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        st.plotly_chart(
            _chart(profiles["0DTE"]["display_rows"], "NET GAMMA 0DTE · corrente", basis),
            use_container_width=True, key="emergency_pine_odte_chart")
    with col_b:
        st.plotly_chart(
            _chart(profiles["Weekly"]["display_rows"], "NET GAMMA Weekly · corrente", basis),
            use_container_width=True, key="emergency_pine_weekly_chart")

    st.download_button(
        "Scarica Pine NET GAMMA corrente (.pine)", data=pine.encode("utf-8"),
        file_name=f"Serafini_SPX_NET_GAMMA_corrente_{day:%Y%m%d}.pine",
        mime="text/plain", key="emergency_pine_export", on_click="ignore",
        use_container_width=True,
    )
    with st.expander("Mostra codice Pine da copiare"):
        st.code(pine, language="pine")
    st.caption("Indicatore statico solo ES/MES: riacquisisci e rigenera "
               "il Pine quando vuoi aggiornare i livelli. "
               "NET GAMMA = (OI Call + Vol Call) × Gamma Call − "
               "(OI Put + Vol Put) × Gamma Put.")

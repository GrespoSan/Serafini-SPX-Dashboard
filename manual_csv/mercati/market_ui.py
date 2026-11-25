"""One shared Streamlit layout for Nasdaq, Gold and Petrolio.

Keep only market-specific metadata different. Acquisition, import, archive
position, title sizes, spacing and empty states are deliberately identical.
No Cboe calculations, price mapping or Pine generation in this module.
"""
import streamlit as st

MARKET_DISPLAY = {
    "NASDAQ": (
        "Nasdaq 100 · NDX",
        "Opzioni NDX/NDXP Cboe  ·  Grafici NQ/MNQ con differenziale NQ−NDX",
    ),
    "GOLD": (
        "Gold · GLD",
        "Opzioni ETF GLD Cboe  ·  Pine GC/MGC con rapporto ETF/future bloccato (proiezione indicativa)",
    ),
    "WTI": (
        "Petrolio · USO",
        "Opzioni ETF USO Cboe  ·  Pine CL/MCL con rapporto ETF/future bloccato (proiezione indicativa)",
    ),
}

UPLOAD_KEYS = {
    "NDX": "ndx_upload",
    "GLD": "gld_cboe_upload",
    "USO": "uso_cboe_upload",
}

PAGE_STYLE = """
<style>
/* FULL-WIDTH dashboard on desktop: two 0DTE/Weekly charts need room.
   Applies identically to Nasdaq, Gold, and Petrolio, including Streamlit's
   modern stMainBlockContainer and legacy .block-container selectors. */
[data-testid="stMainBlockContainer"],
[data-testid="stAppViewContainer"] .block-container {
    max-width: none !important;
    width: 100% !important;
    box-sizing: border-box;
    padding-top: 2rem;
    padding-bottom: 3rem;
    padding-left: clamp(1rem, 2vw, 3rem);
    padding-right: clamp(1rem, 2vw, 3rem);
}
@media (max-width: 800px) {
    [data-testid="stMainBlockContainer"],
    [data-testid="stAppViewContainer"] .block-container {
        padding-top: 1.1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
}
</style>
"""


def render_dashboard_header(markets):
    """The same title/selector block, irrespective of the current market."""
    st.markdown(PAGE_STYLE, unsafe_allow_html=True)
    st.title("Multi-Market · NET GAMMA")
    st.caption("RADAR OPZIONI  ·  NDX / GLD / USO  ·  0DTE & WEEKLY  ·  modello di studio Serafini")
    left, right = st.columns([1.35, 2.65], gap="large")
    with left:
        key = st.selectbox(
            "Sottostante", list(markets),
            format_func=lambda k: markets[k].label,
            key="mm_market",
        )
    with right:
        _, description = MARKET_DISPLAY[key]
        st.caption(description)
    st.divider()
    return key


def render_acquisition_controls(symbol, source_url, show_header=True):
    """Reusable acquisition UI: primary button, collapsed import, source notice.

    Returns only UI intent; market-specific caller does ALL validation/state
    updates and may keep the last usable snapshot if a request fails.
    The archive panel follows immediately afterward in all three callers.
    """
    if symbol not in UPLOAD_KEYS:
        raise ValueError("Sottostante grafico non riconosciuto.")
    if show_header:
        st.subheader(f"Fotografia opzioni Cboe · {symbol}")
    from manual_csv.mercati.cboe_acquisition_pause import CBOE_ACQUISITIONS_PAUSED, PAUSE_MESSAGE
    if CBOE_ACQUISITIONS_PAUSED:
        st.caption(PAUSE_MESSAGE)
    clicked = st.button(
        f"Aggiorna fotografia {symbol} da Cboe · sospeso",
        type="primary",
        key=f"{symbol.lower()}_cboe_fetch",
        disabled=CBOE_ACQUISITIONS_PAUSED,
    )
    with st.expander("Importazione manuale JSON originale", expanded=False):
        st.link_button(f"Apri file {symbol}.json su Cboe", source_url)
        uploaded = st.file_uploader(
            f"Carica file {symbol} (ZIP / JSON / JSON.GZ)",
            type=["json", "gz", "zip"], key=UPLOAD_KEYS[symbol],
        )
    st.caption(
        "Acquisizione diretta solo su richiesta. Le fotografie programmate "
        "Cboe sono consultabili dal selettore Snapshot originale Cboe "
        "quando l'archivio GitHub è configurato."
    )
    return clicked, uploaded


def render_empty_snapshot(symbol):
    if symbol not in UPLOAD_KEYS:
        raise ValueError("Sottostante non riconosciuto.")
    st.info(f"Nessuna fotografia {symbol} caricata. Acquisisci da Cboe, "
            "importa il JSON originale oppure seleziona un rilevamento "
            "dall'archivio, se disponibile.")

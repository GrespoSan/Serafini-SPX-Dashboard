import json
import csv
import io
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from manual_csv.cboe_csv_import import csv_to_snapshot
from manual_csv.mercati.v2_market_engine import render_selected_market
from manual_csv.mercati.v2_universal_cboe import render_equity

MARKETS = {"SP500": "SPX", "Nasdaq": "NDX", "Russell": "RUT", "Dow Jones": "DJX",
           "Gold": "GLD", "Petrolio": "USO", "Azioni": None}
ENGINES = {"NDX": "NASDAQ", "RUT": "RUSSELL", "DJX": "DOW", "GLD": "GOLD", "USO": "WTI"}

def ticker_azione_da_csv(payload: bytes) -> str:
    """Ricava il ticker dalla intestazione Cboe, mai dal nome file o per ipotesi."""
    from manual_csv.cboe_csv_import import InvalidCboeCSV
    if not isinstance(payload, bytes) or not payload:
        raise InvalidCboeCSV("CSV azionario vuoto.")
    try:
        header = payload[:16384].decode("utf-8-sig")
    except UnicodeDecodeError:
        header = payload[:16384].decode("cp1252")
    first_row = next(csv.reader(io.StringIO(header)), [])
    title = first_row[0].strip().upper() if first_row else ""
    matches = set(re.findall(r"\(\s*\^?([A-Z][A-Z0-9.\-]{0,14})\s*\)", title))
    if len(matches) != 1:
        raise InvalidCboeCSV(
            "Ticker azionario non identificabile nell'intestazione Cboe. "
            "Carica il CSV originale con il simbolo tra parentesi, es. (AAPL)."
        )
    return next(iter(matches))


@st.cache_data(max_entries=8, show_spinner=False)
def parse_csv(payload, ticker):
    return csv_to_snapshot(payload, ticker)

def render_market(upload, symbol, equity):
    doc = parse_csv(upload.getvalue(), symbol)
    stamp = datetime.fromisoformat(doc["timestamp"]).replace(tzinfo=timezone.utc)
    day = stamp.astimezone(ZoneInfo("America/New_York")).date()
    rome = stamp.astimezone(ZoneInfo("Europe/Rome")).isoformat()
    raw = json.dumps(doc).encode()
    state = st.session_state
    digest = doc["_import_provenance"]["original_csv_sha256"]
    if state.get("chain_identity") != (symbol, digest):
        for key in list(state):
            if key.startswith(("ndx_", "gld_", "uso_", "universal_", "v2_primary_", "v2_ndx_")):
                del state[key]
        state["chain_identity"] = (symbol, digest)
    if equity:
        provenance = doc.get("_import_provenance") or {}
        excluded = provenance.get("unsupported_contract_roots") or {}
        if excluded:
            details = ", ".join(f"{root}: {count}" for root, count in excluded.items())
            st.warning(
                f"CSV Cboe · Root=All: esclusi {provenance.get('unsupported_contracts_excluded', 0)} "
                f"contratti di altre radici ({details}). "
                f"Il NET GAMMA usa soltanto i contratti standard {symbol}. "
                f"Per un CSV più pulito seleziona **Root = {symbol}** su Cboe."
            )
        render_equity(doc, symbol, day)
        return day
    state[f"v2_archive_capture_nyday_{symbol}"] = day
    if symbol == "NDX":
        state.update(ndx_blob=raw, ndx_name=upload.name, ndx_download_rome=rome,
                     ndx_source="CSV Cboe", ndx_capture_rome_iso=rome)
    elif symbol in ("RUT", "DJX"):
        state[f"v2_primary_{symbol.lower()}_active"] = {"raw": raw, "origin": upload.name, "stamp": rome}
    else:
        prefix = symbol.lower() + "_cboe_"
        for key, value in {"raw": raw, "source": "CSV Cboe", "import_rome": rome,
                           "file": upload.name, "sha256": digest}.items():
            state[prefix + key] = value
    render_selected_market(ENGINES[symbol])
    return day

"""Manual CSV SPX — no cloud writes, no automatic snapshot or MT4 substitution."""
from datetime import datetime
import hashlib
import streamlit as st
from .offline_snapshot import load_local_snapshot, InvalidLocalSnapshot
from .cboe_csv_import import InvalidCboeCSV, csv_coverage_warning
from .gamma_section import render as render_csv_net_gamma


def render():
    with st.expander("ANALISI NET GAMMA — CSV MANUALE", expanded=False):
        st.header("NET GAMMA · formula Serafini")
        st.markdown("### Fotografia opzioni Cboe · SPX · [↗ Apri Cboe](https://www.cboe.com/data/quotes/SPX/options)")
        st.caption("Solo il CSV SPX caricato. Separato dall'archivio automatico Alpaca, Replay e MT4; nessuna pubblicazione su Supabase.")
        upload = st.file_uploader("Carica il CSV della catena opzioni Cboe", type=["csv"], key="manual_csv_upload")
        st.session_state['manual_csv_gex_current_source'] = None
        st.session_state['manual_csv_gex_last_report'] = None
        st.session_state['manual_csv_gex_last_error'] = None
        if upload is None:
            return
        symbol = "SPX"
        try:
            store = load_local_snapshot(upload.name, upload.getvalue())
            warning = csv_coverage_warning(store.import_provenance)
            if warning:
                st.warning(warning)
            csv_bytes = upload.getvalue()
            csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
            # La visualizzazione GEX non deve mai essere una dipendenza
            # necessaria per caricare NET GAMMA, grafici o Pine originale.
            st.session_state['manual_csv_gex_current_source'] = {
                'sha256': csv_sha256, 'symbol': symbol, 'oi_mode': 'auto',
            }
            st.session_state['manual_csv_gex_last_report'] = None
            st.session_state['manual_csv_gex_last_error'] = None
            try:
                # I servizi dati GEX sono separati da csv_gex_panel (solo UI).
                # Proteggere anche gli import: un errore GEX deve lasciare
                # il resto dell'app CSV perfettamente utilizzabile.
                from csv_gex_services import (
                    _cached_gex, _cached_occ, OI_LABELS, DEFAULT_OI_LABEL,
                )
                from .csv_gex_pine import MODE_BY_LABEL
                oi_label = st.session_state.get('manual_csv_gex_oi_source', DEFAULT_OI_LABEL)
                oi_mode = OI_LABELS.get(oi_label, 'auto')
                st.session_state['manual_csv_gex_current_source']['oi_mode'] = oi_mode
                iv_label = st.session_state.get(
                    'manual_csv_gex_iv_units',
                    'Rilevamento automatico (blocco se IV ambigua)',
                )
                iv_mode = MODE_BY_LABEL.get(iv_label, 'auto')
                if oi_mode == "csv":
                    gex_report = _cached_gex(csv_bytes, symbol, iv_mode)
                else:
                    # Non associare OI OCC corrente a una foto CSV storica.
                    from datetime import timezone
                    from .cboe_csv_import import csv_to_snapshot
                    from csv_gex_core.calendar import NY
                    from csv_gex_oi import OiSourceError
                    occ_values, occ_provenance, occ_error = None, None, None
                    try:
                        doc = csv_to_snapshot(csv_bytes, symbol)
                        stamp = datetime.strptime(
                            doc["timestamp"], "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=timezone.utc)
                        if stamp.astimezone(NY).date() != datetime.now(NY).date():
                            raise OiSourceError(
                                "CSV non della seduta odierna: OCC corrente "
                                "non è un dato storico equivalente."
                            )
                        accepted = tuple(sorted(
                            {str(item["option"]) for item in doc["data"]["options"]}
                        ))
                        occ_values, occ_provenance = _cached_occ(symbol, accepted)
                    except (OiSourceError, ValueError, KeyError,
                            RuntimeError, TimeoutError, OSError) as occ_exc:
                        occ_error = str(occ_exc)[:230]
                    gex_report = _cached_gex(
                        csv_bytes, symbol, iv_mode, oi_mode,
                        occ_values, occ_provenance, occ_error
                    )
                st.session_state['manual_csv_gex_last_report'] = (
                    (csv_sha256, symbol, iv_mode, oi_mode), gex_report,
                )
            except Exception as exc:
                # Fallback controllato: il NET GAMMA NON dipende dal GEX.
                st.session_state['manual_csv_gex_last_error'] = (
                    type(exc).__name__ + ': ' + str(exc)[:250]
                )

            week = store.week
            snaps = store.list(f"{week}/snapshots/")
            st.session_state[f"manual_csv_serafini_selected_snapshot_{week}"] = store.filename
            render_csv_net_gamma(store, week, snaps, [], show_heading=False, selector_external=True)
        except (InvalidLocalSnapshot, InvalidCboeCSV, ValueError) as exc:
            st.error(str(exc))

"""Manual CSV multi-market — reference Serafini CSV; SPX-only explicit MT4 publication."""
from datetime import datetime
import hashlib
import streamlit as st
from .offline_snapshot import load_local_snapshot, InvalidLocalSnapshot
from .cboe_csv_import import InvalidCboeCSV, csv_coverage_warning
from .gamma_section import render as render_csv_net_gamma
from .csv_market import MARKETS, render_market, ticker_azione_da_csv


def render(mt4_store=None):
    with st.expander("ANALISI NET GAMMA — CSV MANUALE", expanded=False):
        st.header("NET GAMMA · formula Serafini")
        market = st.selectbox("Mercato", list(MARKETS), key="manual_csv_mercato_selector")
        equity = market == "Azioni"
        symbol = MARKETS[market]
        if symbol in ("SPX", "NDX", "RUT", "DJX", "GLD", "USO"):
            st.markdown(
                f"### Fotografia opzioni Cboe · {symbol} · "
                f"[↗ Apri Cboe](https://www.cboe.com/data/quotes/{symbol}/options)"
            )
        else:
            st.markdown(
                "### Fotografia opzioni Cboe · Azioni · "
                "[↗ Apri Cboe (NVDA)](https://www.cboe.com/data/quotes/NVDA/options)"
            )
        upload = st.file_uploader("Carica il CSV della catena opzioni Cboe", type=["csv"], key=f"manual_csv_upload_{market}")
        st.session_state['manual_csv_gex_current_source'] = None
        st.session_state['manual_csv_gex_last_report'] = None
        st.session_state['manual_csv_gex_last_error'] = None
        if upload is None:
            return
        try:
            if equity:
                symbol = ticker_azione_da_csv(upload.getvalue())
                st.caption(f"Titolo individuato automaticamente dal CSV Cboe: **{symbol}**")
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

            basis = None
            if market == "SP500":
                store = load_local_snapshot(upload.name, upload.getvalue())
                warning = csv_coverage_warning(store.import_provenance)
                if warning:
                    st.warning(warning)
                week = store.week
                snaps = store.list(f"{week}/snapshots/")
                st.session_state[f"manual_csv_serafini_selected_snapshot_{week}"] = store.filename
                result = render_csv_net_gamma(store, week, snaps, [], show_heading=False, selector_external=True)
                basis = result.get('es_spx_basis') if result else None
                if mt4_store is not None and st.session_state.get("role")=="admin" and result:
                    st.caption("MT4: 100 strike reali per scadenza (50+50); il nucleo 25+25 resta invariato.")
                    if st.button("INVIA A MT4",type="primary",key="manual_csv_mt4_send"):
                        try:
                            from .mt4 import build_feed, publish
                            payload=build_feed(result["snapshot"],result["reference_spx"],
                                result["es_spx_basis"],result["basis_info"],
                                st.session_state.get("manual_csv_gex_current_source"),
                                st.session_state.get("manual_csv_gex_last_report"),
                                st.session_state.get("manual_csv_gex_iv_units",""))
                            publish(mt4_store,payload,st.session_state.get("role"))
                            st.success("CSV manuale pubblicato e verificato nel feed privato MT4.")
                        except Exception as exc:
                            st.error("Invio MT4 non riuscito: "+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))
            else:
                render_market(upload, symbol, equity)
            return {'payload': csv_bytes, 'symbol': symbol, 'es_spx_basis': basis}
        except (InvalidLocalSnapshot, InvalidCboeCSV, ValueError) as exc:
            st.error(str(exc))

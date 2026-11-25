"""Pannello Pine condiviso; NET GAMMA originale e GEX CSV restano separati."""
import streamlit as st

from .csv_gex_pine import MODE_BY_LABEL, validate_result, append_csv_gex


def prepare_pine_with_csv_gex(code: str, key: str, *, source_ticker: str,
                              price_factor=1.0, price_shift=0.0) -> str:
    """GEX automatico: stesso CSV e solo scadenze diagnostiche valide.

    Se il GEX non e' utilizzabile restituisce intatto il Pine NET GAMMA.
    """
    source = st.session_state.get("manual_csv_gex_current_source")
    if not source or source.get("symbol") != source_ticker:
        st.warning("GEX non aggiunto: il mercato selezionato non corrisponde al CSV.")
        return code
    label = st.session_state.get(
        "manual_csv_gex_iv_units", "Rilevamento automatico (blocco se IV ambigua)"
    )
    mode = MODE_BY_LABEL.get(label)
    try:
        report, valid = validate_result(
            st.session_state.get("manual_csv_gex_last_report"), source, mode
        )
        output = append_csv_gex(
            code, report, valid,
            price_factor=price_factor, price_shift=price_shift,
        )
    except (TypeError, KeyError, ValueError, OverflowError) as exc:
        st.caption(f"GEX non incluso nel Pine (NET GAMMA invariato): {exc}")
        return code
    available = ", ".join(valid)
    missing = ", ".join(name for name in ("0DTE", "Weekly") if name not in valid)
    st.caption(
        f"GEX CSV diagnostico incluso: **{available}** · origine verificata "
        f"SHA256 **{report['sha256'][:12]}** · orario {report['timestamp_rome']} Roma. "
        "Nel Pine GEX 0DTE acceso e Weekly spento di default; "
        "puoi modificarli nei parametri TradingView."
    )
    if missing:
        st.warning(f"GEX {missing} N/D: scadenza senza qualità sufficiente, "
                   "nessun livello generato per quella scadenza.")
    return output


def render_pine_controls(market: str, code: str, filename: str, key: str, *,
                         source_ticker: str, price_factor=1.0, price_shift=0.0):
    """Download e copia usano lo stesso Pine, con GEX automatico se valido."""
    if not code or not code.startswith("//@version=6"):
        raise ValueError("Codice Pine v6 assente o incompleto.")
    code = prepare_pine_with_csv_gex(
        code, key, source_ticker=source_ticker,
        price_factor=price_factor, price_shift=price_shift,
    )
    download_col, copy_col = st.columns(2, gap="small")
    with download_col:
        st.download_button(
            f"Scarica Pine {market} · fotografia corrente",
            data=code, file_name=filename, mime="text/plain",
            key=f"pine_download_{key}", on_click="ignore",
        )
    with copy_col:
        with st.container(key=f"pine_copy_{key}"):
            with st.expander("Visualizza e copia codice Pine", expanded=False):
                st.caption("Usa l'icona Copia per incollare il sorgente in TradingView.")
                st.code(code, language=None, wrap_lines=False)

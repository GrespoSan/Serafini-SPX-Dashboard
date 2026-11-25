"""Cboe USO module: one-click snapshot, with manual JSON fallback.
No Alpaca, scheduled polling, or conversion to NYMEX CL strikes.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo
import hashlib

import streamlit as st
from manual_csv.mercati.gamma_chart_style import profile_chart, panel_heading, capture_display, spx_shared_gamma_bounds
from manual_csv.mercati.v2_visual_extension import expanded_display

from manual_csv.mercati.v2_continuous_ratio import load_chart_continuous_mapping
from manual_csv.mercati.etf_gamma_axes import chart_price_axes
from manual_csv.mercati.market_ui import render_empty_snapshot

from manual_csv.mercati.uso_cboe import (USO_URL, download_uso, read_uso, expirations, next_weekly,
                      close_previous, current_price, profile, MIN_VALID_COVERAGE)


def _plot(p, reference, common_x, shared_y, *, price_ratio=1.0,
          projected=False, title="0DTE", snapshot_label="", tick_levels=None):
    # Native ETF fallback is not a projected CL price: label it as USO.
    return profile_chart(
        p["rows"], "USO", reference, common_x, shared_y,
        price_scale=price_ratio,
        axis_symbol="CL" if projected else None,
        close_label="Close prec. proiettato" if projected else "Close prec. USO",
        title=title, snapshot_label=snapshot_label, tick_levels=tick_levels)


def _save_snapshot(raw, filename, source):
    """Validate before replacing the previous good snapshot."""
    doc = read_uso(raw)
    dates = expirations(doc)
    if not dates:
        raise ValueError("Nessuna scadenza USO riconosciuta.")
    digest = hashlib.sha256(raw).hexdigest()
    prior = st.session_state.get("uso_cboe_sha256")
    st.session_state["uso_cboe_raw"] = raw
    st.session_state["uso_cboe_file"] = filename
    st.session_state["uso_cboe_sha256"] = digest
    st.session_state["uso_cboe_source"] = source
    st.session_state["uso_cboe_import_rome"] = datetime.now(
        ZoneInfo("Europe/Rome")).isoformat(timespec="seconds")
    if prior != digest:
        # New chain: discard manual previous-close override from older source.
        previous = close_previous(doc)
        st.session_state["uso_cboe_reference"] = (
            f"{previous:.4f}" if previous is not None else "")
        st.session_state.pop("uso_cboe_day", None)
    return prior != digest


def render_uso_cboe():
    raw = st.session_state.get("uso_cboe_raw")
    if not raw:
        render_empty_snapshot("USO")
        return
    try:
        data = read_uso(raw)
        available = expirations(data)
    except Exception as exc:
        st.error(f"File USO non valido: {str(exc)[:260]}")
        return

    stamp = data.get("timestamp")
    st.caption(f"**Fonte:** {st.session_state.get('uso_cboe_source', 'USO.json')} · "
               f"**Acquisizione (Roma):** {st.session_state.get('uso_cboe_import_rome', 'non verificata')} · "
               f"**Timestamp originale Cboe:** {stamp if stamp else 'non disponibile'}")
    if not stamp:
        st.warning("Il file non contiene un timestamp Cboe verificabile. "
                   "La data d'importazione non certifica la freschezza del mercato.")

    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
    archive_day = (
        st.session_state.get("v2_archive_capture_nyday_USO")
        if st.session_state.get("v2_archive_choice_uso") != "manuale"
        else None)
    acquisition_day = archive_day or ny_today
    future = [d for d in available if d >= acquisition_day]
    first = (acquisition_day if acquisition_day in available
             else future[0] if future else available[-1])
    base_day = st.selectbox(
        "Scadenza principale della fotografia selezionata (ora New York)",
        available, index=available.index(first),
        format_func=lambda d: d.strftime("%d/%m/%Y"), key="uso_cboe_day")
    is_0dte = base_day == acquisition_day
    if not is_0dte:
        st.warning(f"La scadenza {base_day:%d/%m/%Y} NON coincide con "
                   f"la data della fotografia {acquisition_day:%d/%m/%Y}: "
                   "profilo di altra scadenza, NON 0DTE del rilevamento.")
    elif acquisition_day != ny_today:
        st.caption(f"0DTE STORICO · fotografia del {acquisition_day:%d/%m/%Y} "
                   "a New York. Non è una catena 0DTE corrente.")
    weekly = next_weekly(base_day, available)
    if weekly is None:
        st.info("Il prossimo venerdì successivo alla scadenza principale "
                "non è presente nel file: Weekly non disponibile.")
    st.caption("Weekly: " + (weekly.strftime("%d/%m/%Y") if weekly else "non disponibile") +
               " (solo venerdì effettivamente presente nella catena Cboe).")

    auto_ref = close_previous(data)
    if auto_ref is None:
        st.warning("Chiusura precedente assente nel JSON; inserisci un riferimento USO verificato.")
    if "uso_cboe_reference" not in st.session_state:
        st.session_state["uso_cboe_reference"] = (
            f"{auto_ref:.4f}" if auto_ref is not None else "")
    reference_text = st.text_input(
        "Chiusura USO della seduta precedente (USD)",
        key="uso_cboe_reference")
    try:
        reference = float(reference_text.replace(",", "."))
        if not (0 < reference < 100_000):
            raise ValueError()
    except (TypeError, ValueError):
        st.error("Serve una chiusura USO precedente valida.")
        return

    band = st.number_input("Fascia strike ± % dal close USO precedente",
                           min_value=0.5, max_value=10.0, value=3.0,
                           step=0.5, key="uso_cboe_band")
    price = current_price(data)
    if price is not None:
        st.caption(f"Prezzo USO nel file: {price:,.2f} USD · "
                   f"Chiusura precedente: {reference:,.2f} USD · "
                   f"Fascia strike ±{band:g}%.")
    else:
        st.caption(f"Chiusura precedente: {reference:,.2f} USD · fascia strike ±{band:g}%.")

    groups = {}
    keys = [("0DTE" if is_0dte else "Principale", base_day)]
    if weekly:
        keys.append(("Weekly", weekly))
    for title, expiry in keys:
        try:
            groups[title] = profile(data, expiry, reference, band_pct=band)
        except Exception as exc:
            st.error(f"{title}: {str(exc)[:260]}")
    if not groups:
        return

    show_extension = st.checkbox(
        "Estensione automatica NET GAMMA — solo grafici e Pine",
        value=True, key="v2_uso_visual_extension",
        help="Estensione dei livelli ETF reali nello stesso file Cboe. "
             "La formula e il CSV dei profili originari non cambiano.")
    visual_groups, extension_info = {}, {}
    for name, original in groups.items():
        if show_extension and original.get("usable"):
            visual_groups[name], extension_info[name] = expanded_display(
                original,
                lambda width, expiry=date.fromisoformat(original["expiry"]): profile(
                    data, expiry, reference, band_pct=width),
                reference, price, band, strike_field="strike", gamma_field="net",
                upper_band_limit=10.0)
        else:
            visual_groups[name], extension_info[name] = original, {"extra": 0}
    good = [v for v in visual_groups.values() if v["usable"]]
    if not good:
        st.warning("Nessun profilo NET GAMMA utilizzabile nella fotografia "
                   "selezionata; non vengono inventati strike o Gamma.")
        return
    # Single choice: continuous CL. Yahoo ETF/CL=F matched M5
    # bars, strictly before this Cboe snapshot. Continuous prices only.
    mapping, mapping_issue = load_chart_continuous_mapping(
        "USO", st.session_state)
    # SPX gamma_section uses one common min/max with 8% spread margin.
    # Independent of bar presentation: no change to original Cboe Gamma.
    xlim = spx_shared_gamma_bounds(good)
    all_strikes = [float(row["strike"]) for p in good for row in p["rows"]]
    ylim = [min(reference * (1 - band / 100), min(all_strikes)),
            max(reference * (1 + band / 100), max(all_strikes))]
    # The same strike ticks on both plots, as in SPX. Never synthesize
    # market levels: ticks are from actual option strikes.
    shared_ticks = sorted({float(row["strike"]) for p in good for row in p["rows"]})
    if len(shared_ticks) > 80:
        step = (len(shared_ticks) + 79) // 80
        shared_ticks = shared_ticks[::step]
    axes = chart_price_axes("USO", mapping, ylim, shared_ticks)
    projected = axes["projected"]
    st.markdown("**NET GAMMA CL CONTINUO · " + ("0DTE" if is_0dte else "Principale (non 0DTE)") + " / Weekly**")
    from manual_csv.mercati.v2_gamma_summary import render_gamma_summary
    render_gamma_summary(good, net_key="net", call_key="call", put_key="put")
    if projected:
        st.caption(
            f"Strike opzioni USO proiettati sul continuo CL con "
            f"rapporto USO/{mapping['future_yahoo_ticker']} "
            f"{axes['price_ratio']:.8f} · barra M5 sincronizzata "
            f"{mapping['bar_ny']} New York. NET GAMMA originali invariati. "
            "Il continuo Yahoo è un proxy del continuo TradingView, "
            "che può differire al rollover.")
    else:
        st.caption(
            "Conversione continua non disponibile per questa fotografia: "
            "nessun livello CL viene stimato o mostrato. "
            "Grafico in strike ETF.")
    st.caption('Scala NET GAMMA comune a 0DTE e Weekly.')
    cols = st.columns(len(groups), gap="medium")
    for (title, p), col in zip(groups.items(), cols):
        with col:
            header, source_details = panel_heading(
                title, p["expiry"], source_timestamp=stamp,
                downloaded_rome=st.session_state.get("uso_cboe_import_rome"),
                source=st.session_state.get("uso_cboe_source", ""))
            st.subheader(header)
            st.caption(source_details)
            st.caption(f"{p['eligible']} contratti in fascia · "
                       f"{p['valid_contracts']} contratti completi · "
                       f"{p['complete_strikes']} strike con Call/Put · "
                       f"copertura {p['coverage']:.0%}")
            if p["missing"]:
                st.caption("Dati scartati: " + ", ".join(
                    f"{name}: {count}" for name, count in p["missing"].items()))
            if not p["usable"]:
                st.warning(f"Profilo non utilizzabile: Gamma mancante/tutto zero, "
                           f"meno di due strike completi o copertura inferiore al "
                           f"{MIN_VALID_COVERAGE:.0%}. Nessun livello inventato.")
                continue
            _, capture_label = capture_display(
                stamp, st.session_state.get("uso_cboe_import_rome"),
                st.session_state.get("uso_cboe_source", ""))
            shown = visual_groups[title]
            extra = extension_info[title].get("extra", 0)
            if extra:
                st.caption(f"Estensione grafica: +{extra} strike autentici; "
                           "valori originali invariati.")
            gamma_x = xlim
            # Never display ETF strikes as CL if prices aren't synchronized.
            if shown["rows"]:
                fig = _plot(shown, reference, gamma_x, axes["y_range"],
                            price_ratio=axes["price_ratio"], projected=projected,
                            title=title, snapshot_label=capture_label,
                            tick_levels=axes["tick_levels"])
                st.plotly_chart(fig, use_container_width=True,
                                key=f"uso_spx_gamma_{title}_{p['expiry']}")
    # Come Nasdaq: comparazione facoltativa Gamma/prezzo, solo 5m chiusi.
    # Quando non esiste un mapping M5 verificato si confronta l'ETF originale,
    # senza fabbricare livelli sul future CL.
    from manual_csv.mercati.gamma_price_compare import render_optional_compare
    render_optional_compare(
        label="USO / CL", key="uso",
        yahoo_ticker="CL=F" if projected else "USO",
        target_day=base_day,
        groups={name: visual_groups[name] for name, group in groups.items()
                if group.get("usable") and visual_groups[name].get("rows")},
        strike_field="strike", gamma_field="net",
        source_name="USO", price_name="CL" if projected else "USO",
        price_scale=axes["price_ratio"], reference_close=reference,
        snapshot_doc=data,
    )
    if not projected:
        st.caption("Confronto su USO originale: nessuna conversione "
                   "fittizia dei livelli alle quotazioni CL.")

    st.caption("NET GAMMA originali delle opzioni USO; conversione grafica "
               "sul continuo CL, non Gamma delle opzioni futures.")
    with st.expander("Approfondimento · Indice di volatilità OVX", expanded=False):
        st.caption("OVX è l'indice Cboe di volatilità implicita a 30 giorni "
                   "delle opzioni USO. Non modifica i Gamma o gli strike dell'ETF.")
        st.link_button("Consulta OVX sul sito Cboe",
                       "https://www.cboe.com/us/indices/dashboard/ovx/")

    # Pine sullo stesso set di strike Cboe dei grafici; la proiezione future
    # e' consentita solo se il mapping M5 ETF/future e' stato verificato.
    from manual_csv.mercati.v2_universal_cboe import _pine as make_etf_pine
    from manual_csv.mercati.pine_ui import render_pine_controls
    usable_original = {title: group for title, group in groups.items()
                       if group.get('usable') and group.get('rows')}
    usable_visual = {title: visual_groups[title] for title in usable_original}
    enlarged_count = sum(len(group['rows']) for group in usable_visual.values())
    selected = (usable_visual if show_extension and enlarged_count <= 480
                else usable_original)
    if show_extension and enlarged_count > 480:
        st.caption('Estensione grafica oltre 480 strike: il Pine utilizza '
                   'soltanto i profili originali, senza tagliare livelli.')
    try:
        pine_groups = {
            title: dict(group, expiry=date.fromisoformat(str(group['expiry'])))
            for title, group in selected.items()
        }
        pine = make_etf_pine(
            "USO", pine_groups, future_mode=projected,
            future_ratio=axes['price_ratio'] if projected else None,
        )
        pine_market = "CL / MCL" if projected else "USO"
        render_pine_controls(
            pine_market, pine,
            f'USO_NET_GAMMA_{base_day.isoformat()}.pine',
            'uso', source_ticker='USO',
            price_factor=axes['price_ratio'] if projected else 1.0,
        )
        if projected:
            st.caption('Pine USO proiettato sul continuo CL / MCL. '
                       'Sono strike delle opzioni ETF, non opzioni sul future; '
                       'ricontrolla il rapporto di conversione al rollover.')
        else:
            st.caption('Pine su strike USO originali: conversione '
                       'CL / MCL non verificata, nessun livello future simulato.')
    except (ValueError, KeyError, TypeError) as exc:
        st.warning(f'Pine USO non generato: {exc}')

    # Keep 4-argument call compatible with a previous module during Streamlit hot reload.
    # The current exporter reads source SHA and Cboe timestamp from session state.
    st.session_state["uso_cboe_timestamp"] = stamp

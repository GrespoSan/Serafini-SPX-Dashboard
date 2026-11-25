"""Pannello GEX diagnostico indipendente, fonte solo CSV caricato."""
from __future__ import annotations
import hashlib
import math

import plotly.graph_objects as go
import streamlit as st
from csv_gex_engine import MODEL_LABEL
from csv_gex_price_display import gex_display_price, gex_price_label
from csv_gex_es_quote import fetch_es_quote
from csv_gex_curve import curve_extrema, quote_inside_curve
from csv_gex_source_display import describe_gex_sources
# Compatibilità con eventuali import precedenti: cache e configurazione dati
# vivono in un modulo autonomo, senza dipendere dalla presentazione Streamlit.
from csv_gex_services import (
    _cached_gex, _cached_occ, OI_LABELS, DEFAULT_OI_LABEL,
)

@st.cache_data(show_spinner=False, max_entries=5, ttl=60)
def _cached_es_quote():
    """Actual delayed CME quote; chart source timestamp is not the fetch time."""
    return fetch_es_quote()



def _number(value):
    return f"{value:,.2f}" if value is not None else "N/D"


def _gex_metric(column, label, value, *, negative=False, key=""):
    """Colora solo il VALORE negativo; label e stile Streamlit restano intatti."""
    if negative:
        with column:
            with st.container(key=f"csv_gex_negative_{key}"):
                st.metric(label, value)
    else:
        column.metric(label, value)


@st.fragment(run_every=60)
def _live_es_curve(report, spot, *, symbol, es_spx_basis, selected):
    # Live quote has its own acquisition clock, independent of CSV snapshot.
    quote=_cached_es_quote() if symbol=="SPX" else None
    if symbol=="SPX":
        if quote and quote.get("price") is not None:
            k=quote["symbol"]
            label=("contratto trimestrale" if quote["contract_kind"]=="EXPLICIT"
                   else "continuo Yahoo: può differire da ESZ26")
            st.caption(
                f"**ES · {quote['price']:,.2f}** · {k} ({label}) · "
                f"{quote['observed_rome']} Roma · "
                f"età osservazione {quote['age_minutes']:.1f} minuti · "
                "Yahoo Finance, quotazione potenzialmente ritardata. "
                "La curva rimane riferita alla fotografia, non viene ricalcolata."
            )
        else:
            st.caption("**Prezzo ES: N/D** — quotazione Yahoo non disponibile/aggiornata. "
                       "Nessun prezzo simulato con il differenziale ES−SPX.")
    if symbol=="SPX" and quote and quote.get("price") is not None and not quote_inside_curve(
            report,quote,symbol=symbol,es_spx_basis=es_spx_basis):
        st.caption("La quotazione ES è fuori dal range ±10% della fotografia: "
                   "prezzo mostrato sopra ma pallino escluso per non alterare la scala.")
    st.plotly_chart(
        _curve_chart(report, spot, symbol=symbol,
                     es_spx_basis=es_spx_basis, es_quote=quote),
        use_container_width=True,key=f"csv_gex_curve_{selected}",
    )


def _curve_chart(report, spot, *, symbol, es_spx_basis=None, es_quote=None):
    """Curva teorica invariata; legenda reale e nessuna etichetta sovrapposta."""
    grid = report["curve"]
    xs = [gex_display_price(row["spot"], symbol, es_spx_basis) for row in grid]
    ys = [row["net_gex"] / 1e9 for row in grid]
    low, high = min(ys + [0]), max(ys + [0])
    padding = max((high - low) * .08, .05)
    ymin, ymax = low - padding, high + padding
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines",
        line=dict(color="#43d6ee", width=3),
        name="Curva NET GEX",
        hovertemplate="Scenario %{x:,.2f}<br>NET GEX %{y:+.3f} mld/1%<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#777d8c", line_width=1)

    def vertical(price, name, color, dash="dash", width=1.5):
        if price is None:
            return
        # Tracce vere anziché annotazioni in alto: si identificano dalla
        # legenda e non si accavallano vicino a spot/wall/flip.
        fig.add_trace(go.Scatter(
            x=[price, price], y=[ymin, ymax],
            mode="lines", name=name,
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=name + " %{x:,.2f}<extra></extra>",
        ))

    def point_on_curve(price):
        """Interpolazione LINEARE soltanto tra i 201 punti già calcolati.

        Serve esclusivamente alla posizione verticale del marker:
        non modifica curve, spot, Gamma, regime o livelli GEX.
        Mai estrapolare un valore fuori dai prezzi della fotografia.
        """
        if price is None or len(xs) < 2:
            return None
        for i in range(1, len(xs)):
            x0, x1 = xs[i - 1], xs[i]
            if x0 <= price <= x1 and x1 > x0:
                weight = (price - x0) / (x1 - x0)
                return ys[i - 1] + weight * (ys[i] - ys[i - 1])
        return None

    def marker_on_curve(price, name, color, edge, size):
        ordinate = point_on_curve(price)
        if ordinate is None:
            return
        fig.add_trace(go.Scatter(
            x=[price], y=[ordinate], mode="markers", name=name,
            marker=dict(size=size, color=color,
                        line=dict(width=1.6, color=edge)),
            hovertemplate=(
                f"{name}<br>Prezzo %{{x:,.2f}}<br>"
                "NET GEX teorico %{y:+.3f} mld/1%<extra></extra>"
            ),
        ))

    display_symbol = gex_price_label(symbol)
    marker_on_curve(
        gex_display_price(spot, symbol, es_spx_basis),
        f"Spot modello GEX · {display_symbol} equivalente" if symbol == "SPX"
        else f"Spot modello GEX · {display_symbol}",
        "#e5e7eb", "#9ca3af", 10,
    )
    for field, label, color, dash in (
        ("call_wall", "Call Wall", "#8fe10a", "solid"),
        ("put_wall", "Put Wall", "#9b4de1", "solid"),
        ("flip", "Gamma Flip", "#c084fc", "dot"),
    ):
        vertical(gex_display_price(report[field], symbol, es_spx_basis),
                 f"{label} · {display_symbol}", color, dash)

    # Prezzo reale futures: fonte/tempo diversi dal CSV. Il pallino indica
    # soltanto il NET GEX teorico in corrispondenza di QUEL prezzo, tramite
    # interpolazione sulla curva immutata. Nessun punto se fuori range.
    if quote_inside_curve(report, es_quote, symbol=symbol,
                          es_spx_basis=es_spx_basis):
        actual = float(es_quote["price"])
        marker_on_curve(
            actual, f"ES Yahoo · {actual:,.2f} ({es_quote['symbol']})",
            "#fb923c", "#fdba74", 12,
        )

    extrema = curve_extrema(report, symbol=symbol, es_spx_basis=es_spx_basis)
    if extrema:
        for key, color, label, position in (
            ("maximum", "#22c55e", "Picco MAX", "top center"),
            ("minimum", "#fb7185", "Picco MIN", "bottom center"),
        ):
            item = extrema[key]
            fig.add_trace(go.Scatter(
                x=[item["price"]], y=[item["gex"] / 1e9],
                mode="markers+text", text=[f"{item['price']:,.2f}"], textposition=position,
                textfont=dict(size=11, color=color),
                marker=dict(size=10, color=color,
                            line=dict(width=1, color="#ffffff")),
                name=label, showlegend=False,
                hovertemplate=(f"{label}: prezzo %{{x:,.2f}}<br>"
                               "NET GEX %{y:+.3f} mld/1%<extra></extra>"),
            ))
    fig.update_layout(
        template="plotly_dark", height=520,
        margin=dict(l=52, r=35, t=92, b=52),
        paper_bgcolor="#0b0a12", plot_bgcolor="#0b0a12",
        xaxis_title=(f"Scenario teorico prezzo {display_symbol}"
                     + (" · basis costante della fotografia" if symbol == "SPX" else "")),
        yaxis_title="NET GEX (mld USD / movimento 1%)",
        legend=dict(orientation="h", x=0, y=1.19, xanchor="left",
                    yanchor="bottom", font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)", traceorder="normal"),
        showlegend=True, hovermode="closest",
    )
    fig.update_yaxes(gridcolor="#2d2e3c", zeroline=False,
                     range=[ymin, ymax])
    fig.update_xaxes(gridcolor="#272936")
    return fig


GEX_STRIKE_WINDOWS = ("±2%", "±5%", "±10%", "Tutti")


def _strike_window(rows, *, spot, symbol, es_spx_basis=None, window="±2%"):
    """Seleziona solo la VISUALIZZAZIONE, mai i dati GEX del motore.

    Percentuali riferite al prezzo equivalente del sottostante nella foto
    CSV. Non si usa la quotazione ES corrente per filtrare un CSV storico.
    """
    if window not in GEX_STRIKE_WINDOWS:
        raise ValueError("Intervallo strike non previsto")
    center = gex_display_price(spot, symbol, es_spx_basis)
    if center is None:
        raise ValueError("Prezzo della fotografia non convertibile")
    priced = [(r, gex_display_price(r["strike"], symbol, es_spx_basis))
              for r in rows]
    if window == "Tutti":
        if not priced:
            return [], center * .98, center * 1.02, center
        vals = [v for _, v in priced]
        lo, hi = min(vals), max(vals)
        pad = max((hi - lo) * .015, center * .0005)
        return priced, lo - pad, hi + pad, center
    pct = {"±2%": .02, "±5%": .05, "±10%": .10}[window]
    lo, hi = center * (1 - pct), center * (1 + pct)
    return [(row, value) for row, value in priced if lo <= value <= hi], lo, hi, center


def _strikes_chart(report, *, symbol, spot, es_spx_basis=None, window="±2%"):
    """GEX per strike specchiato, finestra selezionabile, livelli a destra.

    Usa esattamente report['rows'] e i livelli calcolati, senza ricampionare,
    interpolare o risommare i contratti. Colori e gerarchia visiva del
    riferimento fornito dall'utente, non una copia di codice di terzi.
    """
    shown, low, high, center = _strike_window(
        report["rows"], spot=spot, symbol=symbol,
        es_spx_basis=es_spx_basis, window=window,
    )
    fig = go.Figure()
    levels = [v for _, v in shown]
    # Spessore correlato al passo reale delle quotazioni, senza inventare
    # strike intermedi. Non altera i valori delle barre.
    sorted_levels = sorted(set(levels))
    steps = [b - a for a, b in zip(sorted_levels, sorted_levels[1:])
             if b > a]
    min_step = min(steps) if steps else max((high - low) / 40, .5)
    bar_width = max(min_step * .82, .001)
    y = [price for _, price in shown]

    for key, label, color in (
        ("put_gex", "PUTS −", "#9134d7"),
        ("call_gex", "CALLS +", "#80d80c"),
    ):
        values = [float(r[key]) / 1e9 for r, _ in shown]
        fig.add_trace(go.Bar(
            x=values, y=y, orientation="h", width=bar_width,
            name=label, marker=dict(color=color, line=dict(width=0)),
            hovertemplate=(f"{label} · {gex_price_label(symbol)} %{{y:,.2f}}"
                           "<br>GEX %{x:+.4f} mld USD / 1%<extra></extra>"),
        ))

    maxabs = max([abs(float(r[key]) / 1e9)
                  for r, _ in shown for key in ("call_gex", "put_gex")] or [0])
    x_extent = max(maxabs * 1.10, .005)
    fig.add_vline(x=0, line_color="#6c7281", line_width=1.1)
    display_symbol = gex_price_label(symbol)

    # Riferimenti orizzontali e box a destra come nel campione visivo.
    # I dati originali rimangono invariati, conversione SPX->ES solo asse.
    markers = [
        ("Call Wall", report["call_wall"], "#8fe10a", "solid", "#8fe10a", "#13210c"),
        ("Put Wall", report["put_wall"], "#9634db", "solid", "#9634db", "#ffffff"),
        ("Gamma Flip", report["flip"], "#c084fc", "dot", "#251237", "#e9c7ff"),
        ("Spot modello GEX", spot, "#e2e8f0", "dash", "#e2e8f0", "#111827"),
    ]
    offscreen = []
    for label, raw, color, dash, background, foreground in markers:
        price = gex_display_price(raw, symbol, es_spx_basis)
        if price is None:
            continue
        if not (low <= price <= high):
            offscreen.append(label)
            continue
        fig.add_trace(go.Scatter(
            x=[-x_extent, x_extent], y=[price, price],
            mode="lines", name=f"{label} / {display_symbol}",
            line=dict(color=color, width=1.35, dash=dash),
            hovertemplate=f"{label} / {display_symbol} %{{y:,.2f}}<extra></extra>",
        ))
        fig.add_annotation(
            x=1.014, y=price, xref="paper", yref="y",
            xanchor="left", yanchor="middle",
            text=(f"<b>{label.upper()} / {display_symbol}"
                  f" &nbsp; {price:,.2f}</b>"),
            showarrow=False, align="left",
            bgcolor=background, bordercolor=color,
            borderwidth=1, borderpad=5,
            font=dict(family="Arial", size=11, color=foreground),
        )

    # Asse leggibile anche su schermi 1080p; invece di scrivere ogni strike
    # si mantiene la risoluzione originaria ma si diradano le tacche.
    span = high - low
    dtick = 10 if span <= 350 else 20 if span <= 700 else 50
    fig.update_layout(
        template="plotly_dark", height=760,
        paper_bgcolor="#0b0a12", plot_bgcolor="#0b0a12",
        margin=dict(l=66, r=250, t=88, b=54),
        barmode="relative", bargap=.05,
        xaxis_title="GEX stimato (mld USD / movimento sottostante 1%)",
        yaxis_title=(f"Prezzo equivalente {display_symbol}"
                     if symbol == "SPX" else f"Prezzo {display_symbol}"),
        showlegend=True, hovermode="closest",
        legend=dict(orientation="h", x=0, y=1.15, xanchor="left",
                    yanchor="bottom", font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_xaxes(range=[-x_extent, x_extent], showgrid=True,
                     gridcolor="#272936", zeroline=False,
                     tickformat=".2f")
    fig.update_yaxes(range=[low, high], showgrid=True,
                     gridcolor="#262937", zeroline=False,
                     tickformat=",.0f", dtick=dtick)
    # Informazioni per le didascalie Streamlit; nessun effetto sui calcoli.
    fig.update_layout(meta={
        "strike_visible": len(shown),
        "strike_total": len(report["rows"]),
        "window": window,
        "offscreen_levels": offscreen,
        "spot_display": center,
    })
    return fig


def render(payload: bytes | None, symbol: str | None, *, es_spx_basis=None, state_prefix="") -> None:
    st.caption(
        "**Modulo GEX locale, modello Edge-style.** "
        "Dati di base dal CSV Cboe; Open Interest dal CSV oppure da OCC, "
        "secondo la fonte effettivamente utilizzata. "
        "**Nessun download Alpaca/OPRA.** "
        "NET GAMMA Serafini e GEX rimangono distinti."
    )
    if not payload or not symbol:
        st.info("Carica una fotografia CSV Cboe nel modulo NET GAMMA "
                "per analizzare separatamente il GEX.")
        return
    digest = hashlib.sha256(payload).hexdigest()
    st.selectbox(
        "Fonte Open Interest per il GEX",
        list(OI_LABELS),
        key=state_prefix + "csv_gex_oi_source",
        help=("Auto: prova OCC solo sul CSV della seduta corrente; "
              "se la riconciliazione non supera il 95%, torna al CSV con avviso. "
              "OCC only: nessun fallback. La data OI non è certificata "
              "dal file OCC Series Search.")
    )
    st.markdown(
        f"**Sottostante CSV:** {symbol} · "
        + ("**Livelli visualizzati:** ES/MES · " if symbol == "SPX" else "")
        + f"**Identità CSV:** {digest[:12]}"
    )
    labels = {
        "Rilevamento automatico (blocco se IV ambigua)": "auto",
        "IV decimale · 0,20 = 20%": "decimal",
        "IV percentuale · 20 = 20%": "percent",
    }
    choice = st.selectbox(
        "Unità della volatilità implicita nel CSV",
        list(labels), key=state_prefix + "csv_gex_iv_units",
        help="Rilevamento automatico predefinito. Se l'unità IV è ambigua, "
             "è possibile correggerla qui: l'app ricalcola da sola al cambio.",
    )
    mode = labels[choice]
    oi_mode=OI_LABELS.get(st.session_state.get(state_prefix + "csv_gex_oi_source",
                                               DEFAULT_OI_LABEL),"auto")
    signature = (digest, symbol, mode, oi_mode)
    # Il report è stato preparato automaticamente prima dei Pine.
    # Qui si visualizza soltanto: nessun pulsante, rerun o secondo download.
    saved = st.session_state.get(state_prefix + "csv_gex_last_report")
    if not saved or saved[0] != signature:
        error = st.session_state.get(state_prefix + "csv_gex_last_error")
        if error:
            st.warning("GEX non calcolabile da questo CSV: " + error)
        else:
            st.info("GEX non disponibile per la fotografia selezionata. "
                    "Il NET GAMMA e il relativo Pine restano utilizzabili.")
        return
    info = saved[1]
    oi = info.get("oi_info") or {}
    provenance = describe_gex_sources(info)
    st.markdown(f"#### {provenance['title']}")
    source_col, oi_col = st.columns(2, gap="medium")
    with source_col:
        st.markdown(
            "**Fotografia opzioni — prezzo, IV, contratti**  \n"
            f"{provenance['snapshot_source']}  \n"
            f"**Ora fotografia:** {provenance['snapshot_clock']}"
        )
    with oi_col:
        st.markdown(
            "**Open Interest effettivamente impiegato**  \n"
            f"{provenance['oi_source']}  \n"
            f"**Ora download OCC:** {provenance['oi_download_clock']}"
        )
    st.caption(
        f"**Data reale OI:** {provenance['oi_vintage']}. "
        f"**Alpaca/OPRA:** {provenance['alpaca']}. "
        + provenance["note"]
    )
    st.caption(
        f"**Modello:** {MODEL_LABEL} · "
        "**Calcolo automatico:** report diagnostico separato dal Pine ufficiale · "
        f"Contratti analizzati: {info['csv_contracts']} · "
        f"OI assenti nel CSV: {info['oi_missing']} · "
        f"IV assenti: {info['iv_missing']}."
    )
    if oi.get("effective_source") == "OCC_SERIES_SEARCH":
        st.caption(
            f"**Riconciliazione OCC:** {oi.get('matched')}/{oi.get('eligible')} "
            f"contratti nella banda ±10% spot ({oi.get('match_pct')}%). "
            "Il prezzo e la IV restano quelli della fotografia Cboe."
        )
    if oi.get("fallback_reason"):
        st.warning(
            "**Fallback OCC → CSV dichiarato:** "+str(oi["fallback_reason"]) +
            ". I valori GEX sono calcolati soltanto con l'OI CSV."
        )
    st.caption(info["iv_note"])
    render_report(info, symbol, es_spx_basis=es_spx_basis)


def render_report(info, symbol, *, es_spx_basis=None):
    selected = st.radio("Scadenza GEX", ["0DTE", "Weekly"], horizontal=True,
                        key="csv_gex_selected_expiry")
    report = info["reports"][selected]
    spot_info = info.get("spot_info") or {}
    if spot_info.get("method") == "SPXW_0DTE_PUT_CALL_PARITY_R0_Q0":
        st.caption(f"**SPX implicito dalle opzioni: {info['spot']:,.2f}** · "
                   f"Last CSV (cash precedente): {info['csv_last']:,.2f} · "
                   f"{spot_info['pairs']} coppie SPXW 0DTE coerenti · "
                   "stima diagnostica, non quotazione cash live.")
    price_symbol = gex_price_label(symbol)
    basis_available = (symbol != "SPX" or
                       (es_spx_basis is not None and
                        math.isfinite(float(es_spx_basis))))
    if symbol == "SPX":
        if basis_available:
            st.caption(
                f"**Livelli esclusivamente ES/MES**, con differenziale ES − SPX "
                f"di **{es_spx_basis:+.2f} punti** (stesso valore applicato nel Pine). "
                "La curva GEX resta calcolata su SPX: si trasla solo l'asse prezzi."
            )
        else:
            st.warning(
                "Differenziale ES − SPX non disponibile. "
                "Call Wall, Put Wall e Gamma Flip su ES: N/D. "
                "Il GEX è comunque calcolato sulla fotografia; nessun prezzo SPX "
                "viene presentato come livello ES."
            )
    st.caption(f"**{selected}** · Scadenza: {report['expiry']} · "
               f"Contratti nella fotografia: {report['contracts']} · "
               f"Validi GEX (OI > 0 + IV utilizzabile): {report['valid']}")
    if report["status"] != "DIAGNOSTICO":
        st.warning("Qualità insufficiente — livelli, regime e curva GEX N/D. "
                   + report["reason"])
        metrics = st.columns(4)
        for column, name in zip(
            metrics,
            (f"Call Wall {price_symbol}", f"Put Wall {price_symbol}",
             f"Gamma Flip {price_symbol}", "Regime"),
        ):
            column.metric(name, "N/D")
        st.metric("NET GEX (mld USD / 1%)", "N/D")
        return
    st.warning(
        "**GEX DIAGNOSTICO, NON CERTIFICATO.** " + report["reason"] +
        " I livelli non rappresentano posizioni dealer osservate e non sono "
        "segnali operativi."
    )
    # Lo stile si applica SOLO ai valori negativi del modulo GEX,
    # non alle etichette, ai livelli NET GAMMA o ai grafici.
    st.html("""
    <style>
    [class*="st-key-csv_gex_negative_"] [data-testid="stMetricValue"],
    [class*="st-key-csv_gex_negative_"] [data-testid="stMetricValue"] * {
        color: #ff5b65 !important;
    }
    </style>
    """)
    metrics = st.columns(4)
    for column, field, label, suffix in (
        (metrics[0], "call_wall", f"Call Wall {price_symbol}", "call_wall"),
        (metrics[1], "put_wall", f"Put Wall {price_symbol}", "put_wall"),
        (metrics[2], "flip", f"Gamma Flip {price_symbol}", "flip"),
    ):
        value = (gex_display_price(report[field], symbol, es_spx_basis)
                 if basis_available else None)
        _gex_metric(
            column, label, _number(value),
            negative=value is not None and value < 0, key=suffix,
        )
    _gex_metric(
        metrics[3], "Regime GEX modellato", report["regime"].upper(),
        negative=report["regime"].lower() == "negative", key="regime",
    )
    a, b, c = st.columns(3)
    for column, field, label, suffix in (
        (a, "net_gex", "NET GEX · mld USD / 1%", "net"),
        (b, "call_gex", "CALL GEX · mld USD / 1%", "call"),
        (c, "put_gex", "PUT GEX · mld USD / 1%", "put"),
    ):
        value = report[field]
        _gex_metric(
            column, label, f"{value/1e9:+.3f}",
            negative=value < 0, key=suffix,
        )
    st.caption(
        "Scenario prezzo: 201 punti, ±10% dallo spot; IV, OI e tempo "
        "alla scadenza T congelati. Gamma BSM r=q=0; "
        "Gamma Flip solo se la curva attraversa realmente lo zero."
    )
    if basis_available:
        st.subheader("GEX by Strike · composizione Call / Put")
        st.caption(
            "**PUTS − a sinistra (viola)** · **CALLS + a destra (verde)**. "
            "La linea bianca indica lo spot utilizzato dal modello GEX "
            + ("in equivalente ES; NON è la quotazione ES attuale."
               if symbol == "SPX" else "del sottostante.") +
            " Wall e Gamma Flip sono contrassegnati da linee orizzontali "
            "e cartellini a destra. I grafici utilizzano lo stesso report GEX."
        )
        selected_window = st.segmented_control(
            "Intervallo di prezzo attorno allo spot utilizzato dal modello GEX",
            options=list(GEX_STRIKE_WINDOWS),
            default="±2%", key="csv_gex_strike_window",
            help="Riduce solo gli strike visibili; non modifica GEX, "
                 "Wall, Flip, regime.",
        )
        window = selected_window or "±2%"
        strike_fig = _strikes_chart(
            report, symbol=symbol, spot=info["spot"],
            es_spx_basis=es_spx_basis, window=window,
        )
        st.plotly_chart(
            strike_fig, use_container_width=True,
            key=f"csv_gex_strikes_{selected}",
        )
        meta = strike_fig.layout.meta or {}
        st.caption(
            f"**{meta.get('strike_visible', 0)} strike visualizzati** su "
            f"{meta.get('strike_total', 0)} disponibili · finestra {window} · "
            "unità in miliardi USD per movimento dell'1%. "
            "La selezione agisce solo sulla visualizzazione."
        )
        if meta.get("strike_visible", 0) == 0:
            st.info("Nessuno strike disponibile nella finestra selezionata: "
                    "allarga a ±5%, ±10% o Tutti.")
        if meta.get("offscreen_levels"):
            st.caption(
                "Fuori dalla finestra selezionata: "
                + ", ".join(meta["offscreen_levels"])
                + ". I livelli rimangono calcolati e non vengono modificati."
            )

        st.divider()
        st.subheader("Curva teorica GEX · scenario prezzo")
        st.caption(
            "Legenda superiore: curva NET GEX azzurra, Spot fotografia come pallino "
            "grigio chiaro, ES Yahoo come pallino arancione (se disponibile). "
            "Call Wall, Put Wall e Gamma Flip rimangono linee verticali "
            "di riferimento, non previsioni del percorso futuro."
        )
        _live_es_curve(
            report,info["spot"],symbol=symbol,
            es_spx_basis=es_spx_basis,selected=selected,
        )

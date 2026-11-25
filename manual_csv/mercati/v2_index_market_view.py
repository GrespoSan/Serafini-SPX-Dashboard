"""Dedicated RUT/RTY and DJX/YM principal markets, separate from universal CBOE.

The data is not SPX GEX or futures options. Raw CBOE (OI+V)*Gamma Call-Put
never changes. Only the y-axis undergoes a verified price mapping.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import hashlib
import streamlit as st
import plotly.graph_objects as go

from manual_csv.mercati.v2_universal_cboe import (
    download_once, import_original, read_json, expiries, profile,
)
from manual_csv.mercati.v2_index_basis import compute_basis, CONFIG

NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")


@st.cache_data(ttl=3600, show_spinner=False, max_entries=24)
def _basis_cached(ticker, day, key):
    return compute_basis(ticker, day, key)


def _chart(group, symbol, factor, basis, common_x, common_y, reference, title):
    """SPX-style visual geometry; index/future conversion affects only Y."""
    from manual_csv.mercati.gamma_chart_style import (
        SPX_GREEN, SPX_RED, SPX_ZERO, SPX_CLOSE, SPX_GUIDE,
        SPX_GUIDE_BOLD, SPX_BG,
    )
    rows = group["rows"]
    levels = [float(row["strike"]) * factor + basis for row in rows]
    values = [float(row["net"]) for row in rows]
    if not levels or not all(math.isfinite(x) for x in levels + values):
        raise ValueError("Dati grafico NET GAMMA non validi")
    height = 760
    yspan = max(1.0, common_y[1] - common_y[0])
    levels_sorted = sorted(set(levels))
    gaps = [b - a for a, b in zip(levels_sorted, levels_sorted[1:]) if b > a]
    minstep = min(gaps) if gaps else yspan
    bar_height = max(min(minstep * 0.72, yspan * 7.0 / (height - 135)),
                     yspan / 10000.0)
    fig = go.Figure()
    fig.add_bar(
        x=values, y=levels, width=bar_height, orientation="h",
        marker_color=[SPX_GREEN if x >= 0 else SPX_RED for x in values],
        customdata=[[row["strike"], row["call"], row["put"]] for row in rows],
        hovertemplate=(
            f"Livello {symbol}: %{{y:,.2f}}<br>"
            "Strike indice originale: %{customdata[0]:,.2f}<br>"
            "NET GAMMA: %{x:,.3f}<br>"
            "Call (+): %{customdata[1]:,.3f}<br>"
            "Put (−): %{customdata[2]:,.3f}<extra></extra>"),
    )
    fig.update_layout(
        template="plotly_dark", height=height,
        paper_bgcolor=SPX_BG, plot_bgcolor=SPX_BG, showlegend=False,
        margin=dict(l=15, r=20, t=55, b=32),
        title=dict(text=(
            f"{title} · scadenza {group['expiry']:%d/%m/%Y}<br>"
            "<sup>NET GAMMA per strike — positivo a destra / negativo a sinistra</sup>"
        ), font=dict(size=12, color="#e5e7eb"), x=0.0, xanchor="left"),
        xaxis=dict(title="NET GAMMA (formula Serafini)", range=common_x,
                   fixedrange=True, showgrid=False),
        yaxis=dict(title=f"Livelli {symbol}", range=common_y,
                   autorange=False, type="linear", showgrid=False,
                   tickmode="array", tickvals=levels_sorted,
                   ticktext=[f"{v:g}" for v in levels_sorted]),
        hovermode="closest", bargap=0.1,
    )
    fig.add_vline(x=0, line_width=1, line_color=SPX_ZERO)
    close = reference * factor + basis
    fig.add_hline(y=close, line_dash="dash", line_width=1.5,
                  line_color=SPX_CLOSE)
    for pct in (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5):
        target = close * (1 + pct / 100)
        if common_y[0] <= target <= common_y[1]:
            major = abs(pct) == 1.0
            fig.add_hline(y=target, line_dash="dot",
                          line_width=1.8 if major else 1.2,
                          line_color=SPX_GUIDE_BOLD if major else SPX_GUIDE)
            fig.add_annotation(
                x=0.995, xref="paper", y=target, yref="y",
                text=f"{pct:+.1f}%".replace(".", ","), showarrow=False,
                xanchor="right", yanchor="middle",
                font=dict(color="#e0e6ec", size=10),
                bgcolor="rgba(13,17,23,0.78)")
    fig.add_annotation(
        x=0.995, xref="paper", y=close, yref="y",
        text="Close precedente proiettato", showarrow=False,
        xanchor="right", yanchor="bottom",
        font=dict(color=SPX_CLOSE, size=10),
        bgcolor="rgba(13,17,23,0.85)")
    return fig

def render(ticker):
    if ticker not in CONFIG:
        raise ValueError("Mercato indice sconosciuto")
    info = CONFIG[ticker]
    future = info["future"]
    factor = info["factor"]
    prefix = f"v2_primary_{ticker.lower()}"
    active = st.session_state.get(f"{prefix}_active")
    if not active:
        st.info(f"Nessuna fotografia {ticker} verificata. Usa l'acquisizione manuale o l'importazione.")
        return
    try:
        doc = read_json(active["raw"], ticker)
        dates = expiries(doc, ticker)
        reference = float(doc["data"]["prev_day_close"])
        if not dates or not math.isfinite(reference) or reference <= 0:
            raise ValueError("Scadenze o close precedente CBOE non validi")
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f"Fotografia non utilizzabile: {exc}")
        return
    snapshot_digest = hashlib.sha256(active["raw"]).hexdigest()
    if st.session_state.get(f"{prefix}_snapshot_digest") != snapshot_digest:
        st.session_state[f"{prefix}_snapshot_digest"] = snapshot_digest
        for widget_suffix in ("close", "expiry", "weekly"):
            st.session_state.pop(f"{prefix}_{widget_suffix}", None)
    st.caption(
        f"**File:** {active['origin']} · "
        f"**Acquisizione:** {active['stamp'] or 'non verificabile'} · "
        f"**Timestamp Cboe originale:** {doc.get('timestamp', 'non dichiarato')} "
        "(UTC da intestazione CSV)"
    )
    close = st.number_input(
        f"Close precedente {ticker} · verificare fonte",
        value=reference, min_value=0.01, format="%.4f",
        key=f"{prefix}_close",
    )
    today = datetime.fromisoformat(active["stamp"]).astimezone(NY).date()
    available = [d for d in dates if d >= today]
    preferred = today if today in dates else available[0] if available else dates[-1]
    day = st.selectbox(
        "Scadenza principale (0DTE solo se coincide con la seduta NY)", dates,
        index=dates.index(preferred),
        format_func=lambda d: d.strftime("%d/%m/%Y"),
        key=f"{prefix}_expiry",
    )
    band = st.number_input(
        "Fascia strike ± % dal close precedente (comune)",
        min_value=0.5, max_value=25.0, value=5.0, step=0.5,
        key=f"{prefix}_band",
    )
    from manual_csv.mercati.v2_index_weekly import choose_weekly, weekly_candidates
    following_fridays = weekly_candidates(dates, day)
    preferred_weekly, _preferred_profile, weekly_diagnostics = choose_weekly(
        doc, ticker, day, close, 
        float(band), dates)
    weekly_key = f"{prefix}_weekly"
    weekly_saved_key = f"{prefix}_weekly_saved"
    # Streamlit removes widget keys when a different market is selected.
    # Keep the explicit user's choice outside the selectbox lifecycle, like
    # the persisted basis. None is meaningful ONLY when chosen explicitly.
    weekly_context = (snapshot_digest, day, float(close), float(band))
    allowed_weeklies = [None] + following_fridays
    if st.session_state.get(f"{prefix}_weekly_context") != weekly_context:
        st.session_state[f"{prefix}_weekly_context"] = weekly_context
        st.session_state[weekly_saved_key] = preferred_weekly
        st.session_state[weekly_key] = preferred_weekly
    elif weekly_key not in st.session_state:
        st.session_state[weekly_key] = st.session_state.get(
            weekly_saved_key, preferred_weekly)
    if st.session_state[weekly_key] not in allowed_weeklies:
        st.session_state[weekly_key] = preferred_weekly
        st.session_state[weekly_saved_key] = preferred_weekly
    def _weekly_edited():
        st.session_state[weekly_saved_key] = st.session_state[weekly_key]
    weekly = st.selectbox(
        "Weekly (scadenza CBOE effettivamente presente)",
        allowed_weeklies,
        format_func=lambda d: ("Non disponibile" if d is None else
                               d.strftime("%d/%m/%Y") +
                               ("" if weekly_diagnostics.get(d, {}).get("usable")
                                else " · Gamma insufficienti")),
        key=weekly_key, on_change=_weekly_edited,
        help="Preselezionata la prima scadenza di venerdì con profilo Call/Put "
             "e copertura Gamma realmente utilizzabili. Non genera scadenze fittizie.",
    )
    if not following_fridays:
        st.info(f"{ticker}: nessuna scadenza di venerdì successiva nel file CBOE.")
    elif preferred_weekly is None:
        st.warning(f"{ticker}: presenti scadenze di venerdì, ma nessun profilo "
                   "Weekly utilizzabile con la fascia strike corrente.")
    elif weekly is None:
        st.caption("Weekly disattivata manualmente: nessun profilo Weekly visualizzato.")
    st.subheader(f"Differenziale {future} − {'RUT' if ticker == 'RUT' else 'Dow Jones'}")
    st.caption(
        "Calcolo automatico sulla seduta cash precedente: barre chiuse 15m Massive "
        "future trimestrale e Yahoo indice, sincronizzate per timestamp. "
        "Contratto scelto per la giornata di analisi con controllo rollover."
    )
    result_key = f"{prefix}_basis_result"
    value_key = f"{prefix}_basis_value"
    # Streamlit deletes widget keys when their market is not rendered.
    # Persist the user's editable value separately from the widget lifecycle.
    saved_key = f"{prefix}_basis_saved"
    attempted_key = f"{prefix}_basis_attempted"
    target_key = f"{prefix}_basis_target"
    if st.session_state.get(target_key) != today.isoformat():
        st.session_state[target_key] = today.isoformat()
        st.session_state.pop(result_key, None)
        st.session_state.pop(saved_key, None)
        st.session_state[value_key] = ""
        st.session_state.pop(attempted_key, None)
    def recalc():
        try:
            api = str(st.secrets.get("MASSIVE_API_KEY", "")).strip()
            result = _basis_cached(ticker, today, api)
            st.session_state[result_key] = result
            verified_text = f"{result['basis']:.4f}"
            st.session_state[saved_key] = verified_text
            st.session_state[value_key] = verified_text
            st.session_state.pop(f"{prefix}_basis_error", None)
        except Exception as exc:
            st.session_state.pop(result_key, None)
            st.session_state[f"{prefix}_basis_error"] = str(exc)[:260]
    if not st.session_state.get(attempted_key):
        st.session_state[attempted_key] = True
        recalc()
    if st.button(f"Ricalcola differenziale {future}", key=f"{prefix}_recalc"):
        _basis_cached.clear()
        recalc()
    if st.session_state.get(f"{prefix}_basis_error"):
        st.warning("Differenziale automatico non disponibile: " +
                   st.session_state[f"{prefix}_basis_error"])
    # Rehydrate the widget on return from another market. A deliberately
    # cleared manual value remains cleared; never fabricate a zero basis.
    if value_key not in st.session_state:
        if saved_key in st.session_state:
            st.session_state[value_key] = st.session_state[saved_key]
        elif isinstance(st.session_state.get(result_key), dict):
            # Migrate existing sessions created before this persistence fix.
            recovered = f"{st.session_state[result_key]['basis']:.4f}"
            st.session_state[value_key] = recovered
            st.session_state[saved_key] = recovered
    def _basis_edited():
        st.session_state[saved_key] = st.session_state.get(value_key, "")
    st.text_input(
        f"Differenziale {future} meno indice (punti future, modificabile)",
        key=value_key, on_change=_basis_edited,
        help="Inserisci un basis verificato per il contratto future selezionato. "
             "Un campo vuoto blocca l'esportazione Pine: mai assumere zero.",
    )
    basis_raw = str(st.session_state.get(value_key, "")).strip()
    try:
        basis = float(basis_raw.replace(",", ".")) if basis_raw else None
        if basis is not None and not math.isfinite(basis):
            raise ValueError("non finito")
    except ValueError:
        st.error("Differenziale non valido: inserisci un numero finito")
        return
    result = st.session_state.get(result_key)
    if result:
        st.caption(
            f"{result['source']} · Contratto {result['tv_contract']} · "
            f"seduta {result['reference_date']} · {result['bar_count']} barre comuni · "
            f"media {result['basis']:+.2f}, min {result['minimum']:+.2f}, "
            f"max {result['maximum']:+.2f} · rollover {result['roll_day']}."
        )
    if ticker == "DJX":
        st.info("DJX = DJIA / 100 (specifica CBOE). "
                "Sul future YM/MYM: strike DJX × 100 + differenziale YM−DJIA.")
    else:
        st.caption("Sul future RTY/M2K: strike RUT + differenziale RTY−RUT.")
    groups = {}
    for title, expiry in (("0DTE" if day == today else "Principale", day),
                          ("Weekly", weekly)):
        if expiry is None:
            continue
        try:
            group = profile(doc, ticker, expiry, close, band)
            if group["usable"]:
                groups[title] = group
            else:
                st.warning(f"{title}: Gamma insufficienti; copertura {group['coverage']:.0%}.")
        except ValueError as exc:
            st.warning(f"{title}: {exc}")
    if not groups:
        st.error("Nessun profilo CBOE validato: nessun Pine generato.")
        return
    def _show_index_pine(project_to_future):
        from manual_csv.mercati.v2_universal_cboe import _pine
        from manual_csv.mercati.pine_ui import render_pine_controls
        try:
            script = _pine(
                ticker, groups, future_mode=project_to_future,
                future_basis=basis if project_to_future else 0.0,
            )
            display = future if project_to_future else ticker
            render_pine_controls(
                display, script,
                f'{ticker}_{display}_NET_GAMMA_{day.isoformat()}.pine',
                f'{ticker.lower()}', source_ticker=ticker,
                price_factor=factor if project_to_future else 1.0,
                price_shift=basis if project_to_future else 0.0,
            )
            if not project_to_future:
                st.caption(f'Pine {ticker} su strike indice originali; '
                           f'per il future {future} serve un differenziale verificato.')
        except (ValueError, TypeError, KeyError) as exc:
            st.warning(f'Pine {ticker} non generato: {exc}')

    def _show_index_price_compare():
        from manual_csv.mercati.gamma_price_compare import render_optional_compare
        render_optional_compare(
            label="Russell 2000" if ticker == "RUT" else "Dow Jones",
            key=f"index_{ticker.lower()}",
            yahoo_ticker="RTY=F" if ticker == "RUT" else "YM=F",
            target_day=day, groups=groups,
            strike_field="strike", gamma_field="net",
            source_name=ticker, price_name=future,
            price_scale=factor, price_shift=basis if basis is not None else 0.0,
            reference_close=close, snapshot_doc=doc,
            available=basis is not None,
            unavailable_reason=f"Serve un differenziale {future} verificato; "
                               "non tratto gli strike indice come livelli future.",
        )

    rows = [r for g in groups.values() for r in g["rows"]]
    from manual_csv.mercati.v2_gamma_summary import render_gamma_summary
    render_gamma_summary(groups.values(), net_key="net", call_key="call",
                         put_key="put")
    st.caption(
        f"Gamma originale CBOE · {len(rows)} livelli · close {ticker} {close:.2f}. "
        "Zero interpolazione e zero modifica del Gamma."
    )
    if basis is None:
        st.warning(f"Differenziale non disponibile. Profili sugli strike originali {ticker}.")
        from manual_csv.mercati.gamma_chart_style import spx_shared_gamma_bounds
        native_x = spx_shared_gamma_bounds(groups.values(), field="net")
        strikes = [row["strike"] for row in rows]
        margin = max((max(strikes) - min(strikes)) * .08, .01)
        native_y = [min(strikes) - margin, max(strikes) + margin]
        for (name, group), column in zip(groups.items(), st.columns(len(groups))):
            column.plotly_chart(_chart(group, ticker, 1, 0, native_x, native_y, close, name),
                                key=f"{prefix}_native_{name}_{day}")
        _show_index_price_compare()
        _show_index_pine(project_to_future=False)
        return
    from manual_csv.mercati.gamma_chart_style import spx_shared_gamma_bounds
    common_x = spx_shared_gamma_bounds(groups.values(), field="net")
    projected = [float(row["strike"]) * factor + basis for row in rows]
    span = max(projected) - min(projected)
    margin = max(span * 0.08, 1.0)
    common_y = [min(projected) - margin, max(projected) + margin]
    cols = st.columns(len(groups), gap="medium")
    for (name, group), col in zip(groups.items(), cols):
        with col:
            st.plotly_chart(
                _chart(group, future, factor, basis, common_x,
                       common_y, close, name),
                use_container_width=True,
                key=f"{prefix}_chart_{name}_{day}",
            )
            st.caption(f"{len(group['rows'])} strike originali · "
                       f"copertura {group['coverage']:.0%}")
    _show_index_price_compare()

    _show_index_pine(project_to_future=True)

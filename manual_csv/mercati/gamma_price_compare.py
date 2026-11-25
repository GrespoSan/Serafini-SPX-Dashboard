"""Confronto facoltativo tra NET GAMMA statico Cboe e candele REALI Yahoo 5m.

Nessun download se il toggle e' spento. I prezzi Gamma vengono SOLO convertiti
sull'asse Y con il rapporto/basis gia' calcolato dal modulo chiamante.
Non usa il prezzo intraday per ricalcolare il NET GAMMA, né genera livelli.
"""
from datetime import date, datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
import math
import re

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
FUTURES = {"ES=F", "NQ=F", "RTY=F", "YM=F", "GC=F", "CL=F"}


@st.cache_data(ttl=300, show_spinner=False, max_entries=24)
def load_closed_candles(ticker: str, target_day: date):
    """Yahoo 5m, timestamp della CHIUSURA, senza barre future o incomplete."""
    import yfinance as yf

    if ticker not in FUTURES and not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", ticker):
        raise ValueError("Ticker Yahoo non consentito.")
    if not isinstance(target_day, date):
        raise ValueError("Data di confronto non valida.")
    start = datetime.combine(target_day, time.min, tzinfo=ROME)
    end = start + timedelta(days=1)
    now = datetime.now(UTC)
    if now <= start.astimezone(UTC):
        raise ValueError("La giornata di confronto non e' ancora iniziata.")
    if (now - start.astimezone(UTC)).days > 59:
        raise ValueError("Yahoo 5m non copre questa data: limite circa 59 giorni.")

    raw = yf.download(
        ticker, start=(target_day - timedelta(days=1)).isoformat(),
        end=(target_day + timedelta(days=2)).isoformat(),
        interval="5m", prepost=True, auto_adjust=False,
        progress=False, threads=False,
    )
    if raw is None or raw.empty or raw.index.tz is None:
        raise ValueError(f"Barre {ticker} 5m non disponibili da Yahoo.")
    raw = raw.copy()
    if isinstance(raw.columns, pd.MultiIndex):
        # yfinance restituisce talvolta (prezzo, ticker) oppure (ticker, prezzo).
        wanted = {"Open", "High", "Low", "Close"}
        for depth in range(raw.columns.nlevels):
            layer = raw.columns.get_level_values(depth)
            if wanted.issubset(set(layer)):
                raw.columns = layer
                break
    fields = ("Open", "High", "Low", "Close")
    if any(field not in raw.columns for field in fields):
        raise ValueError("Candele Yahoo senza tutti i campi OHLC.")
    closes_at = raw.index.tz_convert(ROME) + pd.Timedelta(minutes=5)
    candles = pd.DataFrame({
        field.lower(): pd.to_numeric(raw[field], errors="coerce").to_numpy()
        for field in fields
    }, index=closes_at)
    now_rome = pd.Timestamp(now).tz_convert(ROME)
    candles = candles.loc[
        (candles.index >= start) & (candles.index < end)
        & (candles.index <= now_rome)
    ].dropna(subset=[field.lower() for field in fields])
    if candles.empty:
        raise ValueError("Nessuna candela 5m conclusa disponibile nella giornata.")
    candles = candles[
        (candles["high"] >= candles["low"])
        & (candles["low"] <= candles["open"])
        & (candles["open"] <= candles["high"])
        & (candles["low"] <= candles["close"])
        & (candles["close"] <= candles["high"])
        & (candles[["open", "high", "low", "close"]] > 0).all(axis=1)
    ]
    if candles.empty:
        raise ValueError("Candele 5m prive di OHLC coerenti.")
    candles.index.name = "bar_close"
    return candles.reset_index()


def _spx_price_zoom_bounds(candles, levels, close, *, complete=False):
    """Scala della versione SPX replay: prezzi osservati per default, strike se richiesti.

    La soglia fissa 12 pt di ES viene riproporzionata al sottostante:
    12 / 7500 del close, senza deformare il confronto tra asset diversi.
    """
    reference = float(close) if close is not None else float(candles["close"].iloc[-1])
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("Close di riferimento non valido.")
    observed = [float(v) for col in ("low", "high")
                for v in candles[col] if math.isfinite(float(v)) and float(v) > 0]
    if close is not None:
        observed.append(reference)
    active = list(observed)
    if complete or not active:
        active.extend(float(v) for v in levels
                      if math.isfinite(float(v)) and float(v) > 0)
    if not active:
        raise ValueError("Nessun prezzo valido per la scala.")
    lo, hi = min(active), max(active)
    span = hi - lo
    margin = max(12.0 * (reference / 7500.0), span * .12,
                 reference * .004)
    center = (lo + hi) / 2.0
    half = max(span / 2.0 + margin, reference * .005)
    return [center - half, center + half]


def comparison_chart(group, candles, *, strike_field: str, gamma_field: str,
                     source_name: str, future_name: str, group_name: str,
                     price_scale=1.0, price_shift=0.0,
                     reference_close=None, zoom_complete=False):
    """Base SPX: Candele (sx 62%) + NET GAMMA (dx 38%), stesso asse prezzo."""
    scale, shift = float(price_scale), float(price_shift)
    if not (math.isfinite(scale) and scale > 0 and math.isfinite(shift)):
        raise ValueError("Rapporto o differenziale prezzo non valido.")
    rows = group.get("rows") or []
    if not rows:
        raise ValueError("Il profilo Gamma selezionato non ha strike.")
    prices = [float(row[strike_field]) * scale + shift for row in rows]
    gamma = [float(row[gamma_field]) for row in rows]
    if not all(math.isfinite(v) and v > 0 for v in prices):
        raise ValueError("Strike convertiti non validi.")
    if not all(math.isfinite(v) for v in gamma):
        raise ValueError("NET GAMMA non valido.")

    close_level = (float(reference_close) * scale + shift
                   if reference_close is not None else None)
    y_bounds = _spx_price_zoom_bounds(
        candles, prices, close_level, complete=zoom_complete,
    )
    ordered = sorted(set(prices))
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    span = max(prices) - min(prices)
    thickness = max(min(min(gaps) * .72, max(span, 1.0) * .011)
                    if gaps else max(span, 1.0) * .008,
                    max(span, 1.0) * .0001)

    # Plotly usa timestamp civili di Roma; il cutoff OHLC resta timezone-aware.
    bar_times = [
        value.to_pydatetime().astimezone(ROME).replace(
            tzinfo=None).isoformat(timespec="seconds")
        for value in pd.to_datetime(candles["bar_close"])
    ]
    target_day = pd.Timestamp(candles["bar_close"].iloc[0]).tz_convert(ROME).date()
    first_time = datetime.combine(target_day, time.min).isoformat(timespec="seconds")
    last_at = max(
        datetime.combine(target_day, time(18, 35)),
        datetime.fromisoformat(bar_times[-1]) + timedelta(minutes=5),
    )
    end_time = last_at.isoformat(timespec="seconds")
    figure = make_subplots(
        rows=1, cols=2, shared_yaxes=True, horizontal_spacing=.035,
        column_widths=[.62, .38],
        subplot_titles=(f"PREZZO {future_name} · 5 minuti", f"NET GAMMA {group_name}"),
    )
    figure.add_trace(go.Candlestick(
        x=bar_times,
        open=candles["open"], high=candles["high"],
        low=candles["low"], close=candles["close"],
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
        increasing_fillcolor="#22c55e", decreasing_fillcolor="#ef4444",
        name=future_name, showlegend=False,
    ), row=1, col=1)
    figure.add_trace(go.Bar(
        x=gamma, y=prices, width=thickness, orientation="h",
        marker_color=["#21cb82" if v >= 0 else "#f34e59" for v in gamma],
        customdata=[float(row[strike_field]) for row in rows],
        hovertemplate=(
            f"{future_name} %{{y:,.2f}}<br>Strike {source_name}: "
            "%{customdata:,.2f}<br>NET GAMMA %{x:+.6f}<extra></extra>"
        ), name="NET GAMMA", showlegend=False,
    ), row=1, col=2)

    if close_level is not None:
        for column in (1, 2):
            figure.add_hline(
                y=close_level, line_color="#f0cc65",
                line_width=1.4, line_dash="dash", row=1, col=column,
            )

    figure.update_layout(
        height=700, margin=dict(l=18, r=18, t=65, b=55),
        template="plotly_dark", barmode="relative", showlegend=False,
        hovermode="closest",
    )
    figure.update_xaxes(
        title_text=f"{future_name} · 5 minuti · ora Roma {target_day:%d/%m/%Y}",
        type="date", range=[first_time, end_time], autorange=False,
        tick0=first_time, dtick=3 * 60 * 60 * 1000,
        tickformat="%H:%M", rangeslider_visible=False,
        row=1, col=1,
    )
    figure.update_xaxes(
        title_text="NET GAMMA originale", zeroline=True,
        fixedrange=True, row=1, col=2,
    )
    figure.update_yaxes(
        title_text=f"Prezzo {future_name}",
        range=y_bounds, autorange=False, fixedrange=False,
        tickformat=",.2f", row=1, col=1,
    )
    # La sincronizzazione fisica degli assi Y e' fondamentale anche per
    # zoom/pan; non passare due range indipendenti a Plotly.
    figure.update_yaxes(
        matches="y", fixedrange=False, tickformat=",.2f",
        row=1, col=2,
    )
    return figure


def _verified_csv_capture_utc(document):
    """Solo timestamp UTC esplicitamente certificato dal parser CSV Cboe."""
    if not isinstance(document, dict):
        return None
    provenance = document.get("_import_provenance") or {}
    if provenance.get("timestamp_utc_converted") is not True:
        return None
    try:
        return datetime.strptime(
            str(document["timestamp"]), "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=UTC)
    except (KeyError, ValueError, TypeError):
        return None


def render_optional_compare(*, label: str, key: str, yahoo_ticker: str,
                            target_day: date, groups: dict, strike_field: str,
                            gamma_field: str, source_name: str,
                            price_name: str, price_scale=1.0,
                            price_shift=0.0, reference_close=None,
                            available=True, unavailable_reason="",
                            toggle_key=None, radio_key=None,
                            snapshot_doc=None):
    """Widget comune: nessun accesso ai prezzi senza scelta esplicita."""
    st.subheader(f"Confronto NET GAMMA / candele {label}")
    enabled = st.toggle(
        f"Mostra candele {price_name} a sinistra e NET GAMMA a destra",
        value=False, key=toggle_key or f"gamma_candle_compare_{key}",
    )
    if not enabled:
        return
    if not available:
        st.info(unavailable_reason or "Conversione dei livelli non verificata.")
        return
    options = [name for name, group in groups.items() if group.get("rows")]
    if not options:
        st.info("Nessun profilo Gamma utilizzabile per il confronto.")
        return
    selected = st.radio(
        "Profilo da confrontare", options,
        horizontal=True, key=radio_key or f"gamma_candle_profile_{key}",
    )
    zoom_complete = st.checkbox(
        "Mostra tutta la fascia strike (zoom completo)",
        value=False, key=f"gamma_candle_zoom_full_{key}",
        help="OFF: la scala verticale segue le escursioni reali delle candele "
             "come nel Replay SPX. ON: include tutti gli strike selezionati.",
    )
    try:
        with st.spinner(f"Lettura candele {yahoo_ticker} Yahoo a 5 minuti..."):
            candles = load_closed_candles(yahoo_ticker, target_day)
        cutoff_utc = _verified_csv_capture_utc(snapshot_doc)
        if cutoff_utc is not None:
            # Un CSV storico non puo' mostrare candele chiuse DOPO la fotografia.
            candles = candles.loc[
                candles["bar_close"] <= pd.Timestamp(cutoff_utc)
            ].reset_index(drop=True)
            if candles.empty:
                raise ValueError(
                    "Nessuna candela 5m conclusa entro la fotografia Cboe."
                )
        figure = comparison_chart(
            groups[selected], candles, strike_field=strike_field,
            gamma_field=gamma_field, source_name=source_name,
            future_name=price_name, group_name=selected,
            price_scale=price_scale, price_shift=price_shift,
            reference_close=reference_close, zoom_complete=zoom_complete,
        )
        st.plotly_chart(figure, use_container_width=True,
                        key=f"gamma_price_chart_{key}_{selected}_{target_day}")
        st.caption(
            f"Candele Yahoo {yahoo_ticker} a 5 minuti chiusi, "
            f"giornata {target_day:%d/%m/%Y} (Roma). "
            + (f"Fino al CSV Cboe {cutoff_utc.astimezone(ROME):%H:%M} Roma. "
               if cutoff_utc is not None else
               "Orario acquisizione CSV non certificato: confronto descrittivo. ")
            + "Prezzo e NET GAMMA condividono l'asse Y; "
            "gli strike sono originali e il Gamma non viene ricalcolato. "
            "Il continuo Yahoo puo' differire dal TradingView dopo il rollover."
        )
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        st.warning(f"Confronto candele non disponibile: {str(exc)[:230]}. "
                   "Il grafico NET GAMMA originale rimane utilizzabile.")

"""NET GAMMA UNIVERSALE — CBOE: one manual snapshot, no future mapping.

Independent of NDX/GLD/USO, Supabase, archives and scheduled tasks.
Gamma convention matches V2 emergency: (OI + Volume) * Gamma, calls minus puts.
This is not an independently established dealer-positioning or dollar-GEX model.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import requests
import streamlit as st

MAX_BYTES = 60 * 1024 * 1024
TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
CONTRACT = re.compile(r"^([A-Z0-9.-]+)(\d{6})([CP])(\d{8})$")
NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
INDEX_TICKERS = frozenset({"RUT", "DJX"})
WEEKLY_ROOTS = {"RUT": "RUTW", "DJX": "DJXW"}


def format_gamma_it(value, signed=False):
    """Italian thousands/decimal separators; NET signed, Call/Put as contributions."""
    formatted = f"{float(value):+,.2f}" if signed else f"{float(value):,.2f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def validate_ticker(value):
    symbol = str(value or "").strip().upper()
    if not TICKER.fullmatch(symbol):
        raise ValueError("Ticker non valido: usa soltanto lettere, numeri, punto o trattino.")
    return symbol


def _numeric(v):
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def read_json(raw, ticker):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError("File JSON assente o troppo grande (60 MB max).")
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as z:
            raw = z.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("JSON decompresso troppo grande.")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("File non JSON CBOE valido.") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("data"), dict):
        raise ValueError("Oggetto data CBOE assente.")
    actual = str(obj.get("symbol") or "").lstrip("_").upper()
    if actual != ticker:
        raise ValueError(f"Il file contiene {actual or 'un simbolo sconosciuto'}, non {ticker}.")
    options = obj["data"].get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("Catena delle opzioni CBOE vuota.")
    return obj


def parse_contract(item, ticker):
    m = CONTRACT.fullmatch(str(item.get("option", "")).strip().replace(" ", "").upper())
    if not m or m.group(1) not in {ticker, WEEKLY_ROOTS.get(ticker, ticker)}:
        return None
    try:
        expiry = date(2000 + int(m.group(2)[:2]), int(m.group(2)[2:4]), int(m.group(2)[4:]))
    except ValueError:
        return None
    return expiry, m.group(3), int(m.group(4)) / 1000.0


def expiries(doc, ticker):
    return sorted({v[0] for o in doc["data"]["options"] if isinstance(o, dict)
                   if (v := parse_contract(o, ticker)) is not None})


def profile(doc, ticker, expiry, close, band=3.0):
    if not math.isfinite(close) or close <= 0 or not 0 < band <= 25:
        raise ValueError("Chiusura precedente o fascia non valida.")
    lo, hi = close * (1 - band / 100), close * (1 + band / 100)
    sides = defaultdict(dict)
    seen = set()
    matched, complete, positives = 0, 0, 0
    missing = defaultdict(int)
    for o in doc["data"]["options"]:
        if not isinstance(o, dict):
            continue
        part = parse_contract(o, ticker)
        if part is None or part[0] != expiry or not lo - 1e-9 <= part[2] <= hi + 1e-9:
            continue
        _, side, strike = part
        # RUT/RUTW and DJX/DJXW may have separate option contracts
        # on the same expiry, side and strike. Deduplicate by full OCC
        # contract identifier, not by the plotted strike; sum distinct
        # contracts into the same displayed strike/side.
        identifier = str(o.get("option", "")).strip().replace(" ", "").upper()
        if identifier in seen:
            missing["Contratti OCC ripetuti (esclusi)"] += 1
            continue
        seen.add(identifier)
        matched += 1
        gamma, oi, volume = (_numeric(o.get(k)) for k in ("gamma", "open_interest", "volume"))
        if any(v is None for v in (gamma, oi, volume)):
            missing["Gamma/OI/Volume assenti"] += 1
            continue
        complete += 1
        positives += int(gamma > 0)
        sides[strike][side] = sides[strike].get(side, 0.0) + (oi + volume) * gamma
    rows = []
    for strike, pair in sorted(sides.items()):
        if "C" not in pair or "P" not in pair:
            missing["Strike senza coppia Call/Put"] += 1
            continue
        rows.append({"strike": strike, "call": pair["C"], "put": pair["P"],
                     "net": pair["C"] - pair["P"]})
    coverage = complete / matched if matched else 0.0
    usable = coverage >= .70 and positives > 0 and len(rows) >= 2
    return {"expiry": expiry, "rows": rows if usable else [],
            "matched": matched, "complete": complete, "coverage": coverage,
            "missing": dict(missing), "usable": usable, "low": lo, "high": hi}


def import_original(name, content, ticker):
    if len(content) > MAX_BYTES:
        raise ValueError("File troppo grande (60 MB max).")
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [x for x in archive.infolist() if not x.is_dir()
                       and x.filename.lower().endswith((".json", ".json.gz"))
                       and x.file_size <= MAX_BYTES]
            if len(members) != 1:
                raise ValueError("ZIP: deve contenere un solo JSON o JSON.GZ CBOE.")
            with archive.open(members[0]) as fd:
                content = fd.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError("File estratto troppo grande.")
    read_json(content, ticker)
    return content


def download_once(ticker):
    from manual_csv.mercati.cboe_acquisition_pause import require_cboe_acquisition_enabled
    require_cboe_acquisition_enabled()
    # CBOE can restrict requests from hosted servers; never bypass access restrictions.
    source_symbol = "_" + ticker if ticker in INDEX_TICKERS else ticker
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{source_symbol}.json"
    with requests.get(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                      timeout=(8, 55), stream=True) as response:
        if response.status_code in (401, 403, 429):
            raise RuntimeError(f"CBOE HTTP {response.status_code}: usa importazione manuale.")
        response.raise_for_status()
        chunks, length = [], 0
        for chunk in response.iter_content(1024 * 1024):
            length += len(chunk)
            if length > MAX_BYTES:
                raise ValueError("Risposta CBOE oltre 60 MB.")
            chunks.append(chunk)
    raw = b"".join(chunks)
    read_json(raw, ticker)
    return raw


def _chart(group, ticker, common_x):
    rows = group["rows"]
    fig = go.Figure(go.Bar(x=[r["net"] for r in rows],
                           y=[r["strike"] for r in rows], orientation="h",
                           marker_color=["#22c55e" if r["net"] >= 0 else "#ef4444" for r in rows],
                           hovertemplate="Strike %{y:,.2f}<br>NET %{x:+,.4f}<extra></extra>"))
    fig.add_vline(x=0, line_color="#94a3b8")
    fig.update_layout(template="plotly_dark", height=600,
                      title=f"{ticker} · {group['expiry']:%d/%m/%Y}",
                      xaxis_title="NET GAMMA = Call − Put", xaxis_range=common_x,
                      yaxis_title=f"Strike {ticker}",
                      margin=dict(l=20, r=20, t=55, b=40), showlegend=False)
    return fig


def _pine(ticker, groups, *, future_mode=False, future_basis=0.0, future_ratio=None):
    """Same Serafini histogram renderer and one common Gamma scale for both profiles."""
    from manual_csv.mercati.pine_export import _pine_array, _initial_manual_scale, _top_gamma_rows
    from manual_csv.mercati.pine_spx_visual import spx_inputs, SPX_RENDER_FUNCTIONS

    if not groups or len(groups) > 2:
        raise ValueError("Attesi uno o due profili verificati.")
    main_name = next((name for name in groups if name != "Weekly"), None)
    if main_name is None:
        raise ValueError("Scadenza principale CBOE assente.")
    main = groups[main_name]
    weekly = groups.get("Weekly", {"rows": [], "expiry": ""})
    mr, wr = main["rows"], weekly["rows"]
    count = len(mr) + len(wr)
    if not mr or count > 480:
        raise ValueError(f"Livelli Pine {count}: massimo 480; riduci la fascia.")
    for group in (main, weekly):
        for row in group["rows"]:
            if not all(math.isfinite(float(row[k])) for k in ("strike", "net")):
                raise ValueError("Strike/NET GAMMA non finito.")
    is_odte = main_name == "0DTE"
    tc = _top_gamma_rows(mr, "strike", "net", positive=True) if is_odte else []
    tp = _top_gamma_rows(mr, "strike", "net", positive=False) if is_odte else []

    # One common display scale; preserve magnitude comparisons across maturities.
    scale, step = _initial_manual_scale([
        ({"rows": [{"net_gamma": x["net"]} for x in mr]}, 100),
        ({"rows": [{"net_gamma": x["net"]} for x in wr]}, 200)])
    n = lambda value: f"{value:.8f}".rstrip("0").rstrip(".")
    inputs = spx_inputs(n(scale), n(step),
                        tooltip="Scala Gamma unica per 0DTE e Weekly, valori invariati.")
    if not is_odte:
        inputs = inputs.replace('"Mostra 0DTE corrente"', '"Mostra profilo principale"')
        inputs = inputs.replace(
            'showTop3Odte = input.bool(false, "Proietta Top 3 Call / Put 0DTE")',
            'showTop3Odte = false')
    label = "0DTE" if is_odte else "Principale"
    main_title = f"{label} {main['expiry']:%d/%m/%Y}"
    weekly_title = (f"Weekly {weekly['expiry']:%d/%m/%Y}" if wr else "")
    top3_code = (
        '    if showTop3Odte\n'
        '        drawOdteTopProjections(topCalls, topPuts)\n'
        if is_odte else "")
    main_render = (
        f'        if showCurrentOdte\n'
        f'            renderProfile(mainLevels, mainValues, '
        f'bar_index + offsetOdte, "{main_title}", actualScale)\n')
    weekly_render = (
        f'        if showCurrentWeekly\n'
        f'            renderProfile(weeklyLevels, weeklyValues, '
        f'bar_index + weeklyCenter, "{weekly_title}", actualScale)\n'
        if wr else "")
    # Dedicated Russell/Dow market: transform ONLY the plotted price axis.
    # Universal CBOE remains on original index/stock strikes by default.
    transform = future_mode and ticker in ("RUT", "DJX", "GLD", "USO")
    if future_mode and not transform:
        raise ValueError("Conversione future non supportata per questo sottostante.")
    if future_mode and ticker in ("GLD", "USO"):
        if future_ratio is None:
            raise ValueError("Rapporto ETF/future verificato non disponibile.")
        ratio = float(future_ratio)
        if not math.isfinite(ratio) or not 0 < ratio < 1000:
            raise ValueError("Rapporto ETF/future non valido.")
        factor = ratio
    else:
        factor = 100.0 if ticker == "DJX" else 1.0
    if future_mode and ticker in ("RUT", "DJX") and not math.isfinite(float(future_basis)):
        raise ValueError("Differenziale indice/future non valido")
    if transform:
        main_render = main_render.replace("renderProfile(mainLevels,", "renderProfile(plotMainLevels,")
        weekly_render = weekly_render.replace("renderProfile(weeklyLevels,", "renderProfile(plotWeeklyLevels,")
        top3_code = top3_code.replace("drawOdteTopProjections(topCalls, topPuts)",
                                      "drawOdteTopProjections(plotTopCalls, plotTopPuts)")
    if transform and ticker in ("GLD", "USO"):
        future_inputs = (
            f'etfFutureRatio = input.float({factor:.8f}, "Rapporto Future/ETF · M5 sincronizzati", '
            'minval=0.000001, step=0.0001, '
            'tooltip="Proiezione di strike ETF sul continuo futures, non opzioni future. '
            'Controlla l’allineamento e il rollover.")'
        )
    else:
        future_inputs = (
            f'indexFutureBasis = input.float({float(future_basis):.4f}, "Differenziale futuro − indice (punti)", step=0.1, '
            'tooltip="Differenziale congelato su barre cash/future sincronizzate. '
            'Controlla contratto e rollover. Modifica solo livelli visualizzati.")'
            if transform else ""
        )
    future_arrays = (
        'var array<float> plotMainLevels = array.new_float()\n'
        'var array<float> plotWeeklyLevels = array.new_float()\n'
        'var array<float> plotTopCalls = array.new_float()\n'
        'var array<float> plotTopPuts = array.new_float()'
        if transform else ""
    )
    factor_expression = ("etfFutureRatio" if ticker in ("GLD", "USO")
                         else f"{factor:.1f}")
    basis_expression = ("0.0" if ticker in ("GLD", "USO") else "indexFutureBasis")
    future_prepare = (
        '    array.clear(plotMainLevels)\n'
        '    array.clear(plotWeeklyLevels)\n'
        '    array.clear(plotTopCalls)\n'
        '    array.clear(plotTopPuts)\n'
        f'    float basisFactor = {factor_expression}\n'
        f'    float displayBasis = {basis_expression}\n'
        '    for level in mainLevels\n'
        '        array.push(plotMainLevels, level * basisFactor + displayBasis)\n'
        '    for level in weeklyLevels\n'
        '        array.push(plotWeeklyLevels, level * basisFactor + displayBasis)\n'
        '    for level in topCalls\n'
        '        array.push(plotTopCalls, level * basisFactor + displayBasis)\n'
        '    for level in topPuts\n'
        '        array.push(plotTopPuts, level * basisFactor + displayBasis)\n'
        if transform else ""
    )
    # RUT index is displayed on RUT, RTY and M2K, DJX index on DJX,
    # YM and MYM. Futures-only transform is used only in dedicated market.
    ticker_guard = (
        '(syminfo.type == "futures" and (syminfo.root == "RTY" or syminfo.root == "M2K"))'
        if ticker == "RUT" and future_mode else
        '(syminfo.type == "futures" and (syminfo.root == "YM" or syminfo.root == "MYM"))'
        if ticker == "DJX" and future_mode else
        '(syminfo.type == "futures" and (syminfo.root == "GC" or syminfo.root == "MGC"))'
        if ticker == "GLD" and future_mode else
        '(syminfo.type == "futures" and (syminfo.root == "CL" or syminfo.root == "MCL"))'
        if ticker == "USO" and future_mode else
        f'syminfo.ticker == "{ticker}"'
    )
    display_ticker = (
        "RTY" if future_mode and ticker == "RUT" else
        "YM" if future_mode and ticker == "DJX" else
        "GC" if future_mode and ticker == "GLD" else
        "CL" if future_mode and ticker == "USO" else ticker
    )
    # Keep exactly the common SPX renderProfile function and line/label
    # cleanup; adapt only the per-column drawing scale and safety check.
    draw = f'''if barstate.islast and validTicker
    for ln in drawnLines
        line.delete(ln)
    array.clear(drawnLines)
    for lb in drawnLabels
        label.delete(lb)
    array.clear(drawnLabels)
{future_prepare}    float actualScale = gammaPerCandle
    int weeklyCenter = offsetOdte + offsetWeekly
    bool beyondLimit = offsetOdte >= 480 or (showCurrentWeekly and array.size(weeklyValues) > 0 and weeklyCenter >= 480)
    float safeMain = showCurrentOdte ? requiredScale(mainValues, offsetOdte) : 0.0
    float safeWeekly = showCurrentWeekly ? requiredScale(weeklyValues, weeklyCenter) : 0.0
    bool unsafeScale = actualScale < math.max(safeMain, safeWeekly) * 0.999999
    if beyondLimit
        label warn = label.new(bar_index + 10, close, "Distanza profili oltre +500 barre: riduci gli offset.", style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, warn)
    else if unsafeScale
        label warn = label.new(bar_index + 10, close, "Scala Gamma troppo piccola: aumenta Gamma/barra per il profilo interessato.", style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, warn)
    else
{main_render}{weekly_render}{top3_code}'''
    return f'''//@version=6
indicator("NET GAMMA UNIVERSALE {display_ticker} · CBOE", overlay=true, max_lines_count=500, max_labels_count=35)
// Fotografia CBOE statica; rigenerare il Pine per aggiornare.
// Strike originali {ticker}. Eventuale conversione futura SOLO per coordinate visuali.
// Formula: Call (OI+Volume)*Gamma - Put (OI+Volume)*Gamma.
// Scala Gamma comune tra scadenze.
validTicker = {ticker_guard}

{inputs}
{future_inputs}

var array<line> drawnLines = array.new_line()
var array<label> drawnLabels = array.new_label()
var array<float> mainLevels = {_pine_array(mr, "strike")}
var array<float> mainValues = {_pine_array(mr, "net")}
var array<float> weeklyLevels = {_pine_array(wr, "strike")}
var array<float> weeklyValues = {_pine_array(wr, "net")}
var array<float> topCalls = {_pine_array(tc, "strike")}
var array<float> topPuts = {_pine_array(tp, "strike")}
{future_arrays}

{SPX_RENDER_FUNCTIONS}

{draw}
'''

def render_equity(doc, ticker, now):
    dates = expiries(doc, ticker)
    if not dates:
        st.error("Nessuna scadenza riconosciuta.")
        return
    close = float(doc["data"]["prev_day_close"])
    future = [d for d in dates if d >= now]
    initial = now if now in dates else (future[0] if future else dates[-1])
    main_exp = st.selectbox("Scadenza da analizzare", dates, index=dates.index(initial),
                            format_func=lambda d: d.strftime("%d/%m/%Y"),
                            key=f"universal_expiry_{ticker}")
    label = "0DTE" if main_exp == now else "Scadenza selezionata (non 0DTE)"
    weekly_day = main_exp + timedelta(days=((4 - main_exp.weekday()) % 7 or 7))
    weekly = weekly_day if weekly_day in dates else None
    band = st.number_input("Fascia strike ± % dal close precedente",
                           min_value=0.5, max_value=25.0, value=10.0, step=0.5,
                           key="universal_cboe_band")
    groups = {}
    for name, exp in [(label, main_exp), ("Weekly", weekly)]:
        if exp is None:
            continue
        group = profile(doc, ticker, exp, close, band)
        if group["usable"]:
            groups[name] = group
        else:
            st.warning(f"{name}: dati insufficienti (copertura {group['coverage']:.0%}, "
                       f"contratti {group['matched']}, coppie {len(group['rows'])}).")
    if not groups:
        st.error("Nessun profilo affidabile; non calcolo NET GAMMA.")
        return
    allrows = [r for g in groups.values() for r in g["rows"]]
    net = sum(r["net"] for r in allrows)
    calls = sum(r["call"] for r in allrows)
    puts = sum(r["put"] for r in allrows)
    a, b, c = st.columns(3)
    a.metric("NET GAMMA · Call − Put", format_gamma_it(net, signed=True))
    b.metric("GAMMA CALL (+)", format_gamma_it(calls, signed=True))
    c.metric("GAMMA PUT (−)", "−" + format_gamma_it(puts))
    st.caption(f"Close precedente {close:,.2f} · prezzo CBOE "
               f"{doc['data'].get('current_price', 'N/D')} · Gamma non monetario. "
               "Il NET totale somma solo i profili visualizzati, non tutta la catena.")
    from manual_csv.mercati.gamma_chart_style import spx_shared_gamma_bounds
    shared_x = spx_shared_gamma_bounds(groups.values(), field="net")
    st.caption("Scala NET GAMMA comune per tutte le scadenze: lunghezze confrontabili.")
    columns = st.columns(len(groups))
    for (name, group), col in zip(groups.items(), columns):
        with col:
            st.plotly_chart(_chart(group, ticker, shared_x), use_container_width=True,
                            key=f"universal_chart_{ticker}_{name}_{group['expiry']}")
            st.caption(f"{len(group['rows'])} strike Call/Put · "
                       f"copertura {group['coverage']:.0%} · "
                       f"contratti completi {group['complete']}/{group['matched']}")
    # Confronto su prezzi reali del titolo, senza usare un basis future.
    from manual_csv.mercati.gamma_price_compare import render_optional_compare
    render_optional_compare(
        label=ticker, key="equity_" + ticker, yahoo_ticker=ticker,
        target_day=now, groups=groups,
        strike_field="strike", gamma_field="net",
        source_name=ticker, price_name=ticker,
        reference_close=close, snapshot_doc=doc,
    )

    try:
        # Solo sezione Universale: nessun differenziale ETF/indice/future.
        # Non modificare _pine: il ramo future_mode serve a Russell/Dow.
        pine = _pine(ticker, groups, future_mode=False)
        from manual_csv.mercati.pine_ui import render_pine_controls
        render_pine_controls(
            ticker, pine, f"{ticker}_NET_GAMMA_CBOE.pine", "equity",
            source_ticker=ticker,
        )
        st.caption("Pine statico: strike originali sul grafico del solo ticker inserito. "
                   "Non contiene conversioni né aggiornamento automatico.")
    except ValueError as exc:
        st.warning(f"Pine non generato: {exc}")
    st.caption("Modello sperimentale di posizionamento Gamma, non esposizione dealer verificata. "
               "La disponibilità del download diretto dipende da CBOE e dalla rete del server.")

"""Regime VIX da settlement Cboe del trading day precedente.

Si usano SOLO contratti VX mensili STANDARD, senza scadenze weekly.
La percentuale e' (M2/M1 - 1)*100; le opzioni Cboe SPX restano intatte.
"""
import csv
import io
import math
import re
from datetime import date, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

CSV_URL = "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv"
SOURCE_PAGE = "https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily"
MONTHLY = re.compile(r"(?<![A-Z0-9])VX/[FGHJKMNQUVXZ][0-9]{1,2}(?![A-Z0-9])")
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2}/20\d\d)\b")
DECIMAL = re.compile(r"(?<![A-Za-z0-9])\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![A-Za-z0-9])")


def _parse_settlements(text, settlement_day):
    """Estrae expiry e settlement dalle righe VX mensili del CSV Cboe.

    Filtro intenzionalmente restrittivo: VX43/X6 e VXM/X6 non sono
    i futures mensili VX/X6. Non usa date di scadenza gia' trascorse.
    """
    values = {}
    for cells in csv.reader(io.StringIO(text)):
        joined = " | ".join(c.strip() for c in cells)
        symbol = MONTHLY.search(joined)
        if not symbol:
            continue
        tail = joined[symbol.end():]
        found = DATE.search(tail)
        if not found:
            continue
        raw_day = found.group(1)
        try:
            expiry = (date.fromisoformat(raw_day) if "-" in raw_day
                      else date(int(raw_day.split("/")[2]), int(raw_day.split("/")[0]), int(raw_day.split("/")[1])))
        except ValueError:
            continue
        if expiry <= settlement_day:
            continue
        after_date = tail[found.end():]
        # Il primo numero DOPO la data di scadenza e' il prezzo settlement.
        val = DECIMAL.search(after_date)
        if not val:
            continue
        try:
            settlement = float(val.group().replace(",", ""))
        except ValueError:
            continue
        if not 0 < settlement < 200:
            continue
        values[(symbol.group(), expiry)] = settlement
    ordered = sorted(((expiry, symbol, price)
                      for (symbol, expiry), price in values.items()), key=lambda x:x[0])
    if len(ordered) < 2:
        raise ValueError("Cboe: impossibile identificare due futures VX mensili validi nel CSV")
    front, second = ordered[:2]
    return {
        "m1_symbol": front[1], "m1_expiry": front[0].isoformat(), "m1": front[2],
        "m2_symbol": second[1], "m2_expiry": second[0].isoformat(), "m2": second[2],
        "contango_pct": (second[2] / front[2] - 1) * 100,
    }


@st.cache_data(show_spinner=False, ttl=21600, max_entries=80)
def _download_settlement(trading_day):
    url = CSV_URL + "?" + urlencode({"dt": trading_day.isoformat()})
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,text/plain,*/*",
    })
    with urlopen(request, timeout=12) as response:
        raw = response.read(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError("CSV Cboe troppo grande")
    text = raw.decode("utf-8-sig", errors="replace")
    result = _parse_settlements(text, trading_day)
    result["day"] = trading_day.isoformat()
    return result


@st.cache_data(show_spinner=False, ttl=21600, max_entries=70)
def previous_vix_curve(target_day):
    """Legge la PRIMA seduta valida PRIMA di target_day, mai dati odierni."""
    errors = []
    for lag in range(1, 8):
        candidate = target_day - timedelta(days=lag)
        if candidate.weekday() >= 5:
            continue
        try:
            return _download_settlement(candidate)
        except Exception as exc:
            errors.append(f"{candidate.isoformat()} ({type(exc).__name__})")
    raise ValueError("Nessun settlement VX mensile Cboe confermato nei 7 giorni precedenti: "
                     + ", ".join(errors))


def breakout_buffer_points(contango_pct):
    """Regola configurata: Backwardation 15, Contango 10 punti.

    La percentuale deriva dalla curva futures VX mensili (M2/M1 - 1)*100.
    A zero esatto si segue la classificazione non-negativa della dashboard.
    """
    pct = float(contango_pct)
    if not math.isfinite(pct):
        raise ValueError("Contango VX non valido")
    return 15 if pct < 0 else 10


def combined_breakout_buffer(contango_pct=None, daily_range_pct=None):
    """Buffer STANDARD: 15 se backwardation O range Daily >=1%; altrimenti 10.

    Se manca una fonte, 15 e' comunque determinabile dalla condizione
    disponibile di alta volatilita'; 10 richiede entrambe le fonti.
    Questa non e' una regola di ingresso né gestisce eccezioni discrezionali.
    """
    def finite_or_none(value):
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Dato di volatilita non finito")
        return number

    pct = finite_or_none(contango_pct)
    daily = finite_or_none(daily_range_pct)
    if daily is not None and daily < 0:
        raise ValueError("Range Daily negativo")
    reasons = []
    if pct is not None and pct < 0:
        reasons.append("Backwardation VX")
    if daily is not None and daily >= 1.0:
        reasons.append("Range Daily precedente >= 1%")
    if reasons:
        buffer = 15
        status = "DETERMINATO"
    elif pct is not None and daily is not None:
        buffer, status = 10, "DETERMINATO"
        reasons.append("Contango VX e range Daily precedente < 1%")
    else:
        buffer, status = None, "NON DETERMINABILE"
        reasons.append("Dati insufficienti per confermare la bassa volatilita")

    return {
        "status": status,
        "buffer_es_points": buffer,
        "reason": "; ".join(reasons),
        "rule": "15 se VX in backwardation o High-Low Daily >=1% del Close; altrimenti 10",
        "inputs": {"vx_contango_pct": pct, "previous_es_daily_range_pct": daily},
        "note": ("Buffer di copertura standard indicativo; Serafini puo' "
                 "contestualizzare livelli e distanze in modo discrezionale."),
    }


def volatility_context(target_day):
    """Unica fonte per widget e report: non alterare i calcoli NET GAMMA."""
    from daily_volatility import previous_es_daily_range

    vx = {"status": "NON DISPONIBILE"}
    daily = {"status": "NON DISPONIBILE"}
    pct = daily_pct = None
    try:
        observed = previous_vix_curve(target_day)
        pct = float(observed["contango_pct"])
        vx = {**observed, "status": "VALIDO",
              "regime": "BACKWARDATION" if pct < 0 else "CONTANGO",
              "note": "Cboe futures VX mensili: settlement precedente; non VIX spot."}
    except Exception as exc:
        vx["reason"] = type(exc).__name__
    try:
        daily = previous_es_daily_range(target_day)
        daily_pct = daily["range_pct"]
    except Exception as exc:
        daily = {"status": "NON DISPONIBILE", "reason": type(exc).__name__,
                 "source": "Yahoo Finance ES=F Daily"}
    decision = combined_breakout_buffer(pct, daily_pct)
    vx["breakout_buffer_es_points"] = decision["buffer_es_points"]
    return {"vix_curve": vx, "previous_daily_range": daily,
            "breakout_buffer": decision}


def render(target_day):
    """Riquadro compatto come riferimento grafico condiviso, VX+Daily."""
    from html import escape

    ctx = volatility_context(target_day)
    vx, daily, decision = (ctx["vix_curve"], ctx["previous_daily_range"],
                           ctx["breakout_buffer"])
    vx_ok = vx.get("status") == "VALIDO"
    daily_ok = daily.get("status") == "VALIDO"
    pct = vx.get("contango_pct") if vx_ok else None
    if daily_ok:
        range_value = (f"{daily['range_points']:,.2f}".replace(",", "X")
                       .replace(".", ",").replace("X", "."))
        range_pct = f"{daily['range_pct']:.2f}".replace(".", ",")
        daily_value = f"{range_value} pt · {range_pct}%"
        daily_date = daily["day"]
    else:
        daily_value, daily_date = "Non disponibile", "—"
    if vx_ok:
        regime = vx["regime"].capitalize()
        vx_value = f"{regime} ({pct:+.2f}%)".replace(".", ",")
        vx_date = vx.get("day", "—")
    else:
        vx_value, vx_date = "Non disponibile", "—"

    points = decision["buffer_es_points"]
    is_high = points == 15
    value = f"{points} punti" if points is not None else "Da determinare"
    highlight = "#ff676d" if is_high else ("#56d49e" if points == 10 else "#e5b65c")
    detail = escape(decision["reason"])
    card = f"""
<style>
.spx-vol-card {{
    border:1px solid #34363c; border-radius:18px; padding:24px 26px;
    background:#090a0c; color:#f4f5f6; margin:12px 0 8px;
    font-family:inherit;
}}
.spx-vol-card .spx-vol-title {{
    font-size:1.15rem; font-weight:750; letter-spacing:.015em;
    margin:0 0 18px;
}}
.spx-vol-card .spx-vol-row {{
    display:flex; justify-content:space-between; align-items:baseline;
    gap:15px; border-top:1px solid #292b30; padding:15px 0;
}}
.spx-vol-card .spx-vol-label {{font-size:.96rem; color:#c3c6cc;}}
.spx-vol-card .spx-vol-value {{font-size:1.02rem; font-weight:620; text-align:right;}}
.spx-vol-card .spx-vol-bottom {{
    display:flex; justify-content:space-between; gap:20px; align-items:end;
    border-top:1px solid #292b30; padding-top:18px;
}}
.spx-vol-card .spx-vol-big {{
    font-size:2rem; font-weight:750; color:{highlight}; line-height:1.15;
    margin-top:7px;
}}
.spx-vol-card .spx-vol-reason {{
    color:#c3c6cc; max-width:58%; font-size:.94rem; text-align:right;
}}
.spx-vol-card .spx-vol-foot {{
    color:#8c929d; font-size:.76rem; margin-top:16px; line-height:1.5;
}}
@media(max-width:640px) {{
    .spx-vol-card {{padding:17px;}}
    .spx-vol-card .spx-vol-row,.spx-vol-card .spx-vol-bottom {{
        gap:8px; flex-wrap:wrap;
    }}
    .spx-vol-card .spx-vol-reason {{max-width:100%; text-align:left;}}
}}
</style>
<div class="spx-vol-card">
  <div class="spx-vol-title">VOLATILITÀ E BUFFER OPERATIVO</div>
  <div class="spx-vol-row">
    <span class="spx-vol-label">Range Daily precedente</span>
    <span class="spx-vol-value">{escape(daily_value)}</span>
  </div>
  <div class="spx-vol-row">
    <span class="spx-vol-label">Regime VX precedente</span>
    <span class="spx-vol-value">{escape(vx_value)}</span>
  </div>
  <div class="spx-vol-bottom">
    <div>
      <div class="spx-vol-label">Buffer standard</div>
      <div class="spx-vol-big">{escape(value)}</div>
    </div>
    <div class="spx-vol-reason">{detail}</div>
  </div>
  <div class="spx-vol-foot">
    Daily ES=F Yahoo: {escape(daily_date)} · VX Cboe: {escape(vx_date)}.
    High−Low / Close della seduta precedente; soglia 1%.
    Fonti indipendenti, nessun dato della seduta analizzata.
    Il Daily Yahoo può differire dal grafico ES1! su TradingView.
  </div>
</div>"""
    st.markdown(card, unsafe_allow_html=True)
    if not vx_ok or not daily_ok:
        unavailable = []
        if not daily_ok:
            unavailable.append(f"Daily ES ({daily.get('reason', 'fonte indisponibile')})")
        if not vx_ok:
            unavailable.append(f"VX ({vx.get('reason', 'fonte indisponibile')})")
        st.caption("Fonte non disponibile: " + ", ".join(unavailable) +
                   ". Il buffer non viene forzato a 10 punti.")
    return pct

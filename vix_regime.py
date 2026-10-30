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


def combined_breakout_buffer(contango_pct=None, daily_change_pct=None):
    """ES breakout buffer from yesterday's signed CLOSE/CLOSE and VX regime.

    15 punti: previous Daily ES Close/Close strictly below -1%, OR VX
    backwardation. The nonnegative High-Low range does NOT choose 10/15.
    With sources incomplete, do not assert low volatility or force 10.
    The buffer is indicative and contextual, not an entry signal.
    """
    def finite_or_none(value):
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Dato di volatilità non finito")
        return number

    pct = finite_or_none(contango_pct)
    daily = finite_or_none(daily_change_pct)
    reasons = []
    if pct is not None and pct < 0:
        reasons.append("Backwardation VX")
    if daily is not None and round(daily, 6) < -1.0:
        reasons.append("Ribasso Close/Close seduta precedente oltre -1%")
    if reasons:
        buffer, status = 15, "DETERMINATO"
    elif pct is not None and daily is not None:
        buffer, status = 10, "DETERMINATO"
        reasons.append("VX non in backwardation e Close/Close non sotto -1%")
    else:
        buffer, status = None, "NON DETERMINABILE"
        reasons.append("Dati insufficienti per escludere le condizioni di alta volatilità")

    return {
        "status": status,
        "buffer_es_points": buffer,
        "reason": "; ".join(reasons),
        "rule": "15 se VX in backwardation o ES Close/Close seduta precedente < -1%; altrimenti 10 se entrambi verificati",
        "inputs": {"vx_contango_pct": pct,
                   "previous_es_daily_change_pct": daily},
        "note": ("Buffer standard indicativo basato sulla precisazione del metodo Serafini; "
                 "non è una regola universale né un segnale di ingresso."),
    }


def volatility_context(target_day):
    """Unica fonte per widget e report: non alterare i calcoli NET GAMMA."""
    from daily_volatility import previous_es_daily_range

    vx = {"status": "NON DISPONIBILE"}
    daily = {"status": "NON DISPONIBILE"}
    pct = daily_change = None
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
        daily_change = daily["change_pct"]
    except Exception as exc:
        daily = {"status": "NON DISPONIBILE", "reason": type(exc).__name__,
                 "source": "Massive Futures REST · ES 1session"}
    decision = combined_breakout_buffer(pct, daily_change)
    vx["breakout_buffer_es_points"] = decision["buffer_es_points"]
    return {"vix_curve": vx, "previous_daily_range": daily,
            "breakout_buffer": decision}


def signed_daily_heading(change_pct):
    """Separate clear session performance from its daily range/volatility."""
    base = "Andamento e volatilità — seduta precedente"
    if change_pct is None or not math.isfinite(float(change_pct)):
        value = "N/D"
        color = "#b6bcc5"
    else:
        value = f"{float(change_pct):+.2f}%".replace(".", ",")
        color = ("#56d49e" if change_pct > 0 else
                 "#ff676d" if change_pct < 0 else "#b6bcc5")
    return (
        f'<h4 style="font-size:1.4rem;font-weight:700">{base}</h4>'
        '<div style="font-size:1rem;padding:0 0 10px;color:#c3cbd5">'
        'Variazione chiusura della seduta (Close/Close): '
        f'<span style="color:{color};font-weight:750">{value}</span></div>'
    )


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
        raw_range = float(daily["range_points"])
        range_value = (
            f"{int(round(raw_range))}" if abs(raw_range - round(raw_range)) < 0.005
            else f"{raw_range:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        range_pct = f"{daily['range_pct']:.2f}".replace(".", ",")
        daily_value = f"{range_value} pt · {range_pct}%"
        daily_date = daily["day"]
        range_trend = str(daily.get("range_trend") or "N/D")
        previous_range = daily.get("previous_range_pct")
        range_comparison = (
            f"{range_trend} · prima {previous_range:.2f}%".replace(".", ",")
            if previous_range is not None else "Confronto non disponibile"
        )
    else:
        daily_value, daily_date = "Non disponibile", "—"
        range_comparison = "Confronto non disponibile"
    # Unlike the nonnegative High−Low range, the close-to-close change
    # communicates whether the prior ES session ended higher or lower.
    close_change = daily.get("change_pct") if daily_ok else None
    # Both figures come from one Massive bar set; no second source or call.
    st.markdown(signed_daily_heading(close_change), unsafe_allow_html=True)
    if daily_ok and daily.get("previous_close") and daily.get("previous_close_day"):
        st.caption(
            f"Massive Futures {daily['ticker']} · 1session · "
            f"chiusura {daily['day']}: {daily['close']:,.2f} / "
            f"chiusura {daily['previous_close_day']}: {daily['previous_close']:,.2f}. "
            "È Close/Close sullo stesso contratto, NON settlement/settlement. "
            "Verificare l'allineamento della scadenza a ES1!."
        )
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
  border:1px solid #34363c; border-radius:13px; padding:14px 18px;
  background:#090a0c; color:#f4f5f6; font-family:inherit;
}}
.spx-vol-grid {{
  display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
  gap:0; align-items:center;
}}
.spx-vol-kpi {{padding:5px 18px; min-width:0; border-left:1px solid #292b30;}}
.spx-vol-kpi:first-child {{padding-left:0; border-left:0;}}
.spx-vol-kpi .label {{font-size:.82rem; color:#b6bcc5; margin-bottom:5px;}}
.spx-vol-kpi .number {{font-size:1.15rem; font-weight:740; overflow-wrap:anywhere;}}
.spx-vol-kpi .range-direction {{font-size:.79rem;color:#abb6c4;margin-top:8px;line-height:1.5;}}
.spx-vol-kpi .buffer {{color:{highlight}; font-size:1.3rem;}}
.spx-vol-foot {{
  border-top:1px solid #292b30; padding-top:9px; margin-top:12px;
  color:#949ba6; font-size:.74rem; line-height:1.4;
}}
@media(max-width:700px) {{
  .spx-vol-grid {{grid-template-columns:1fr;}}
  .spx-vol-kpi {{padding:10px 0; border-left:0; border-top:1px solid #292b30;}}
  .spx-vol-kpi:first-child {{border-top:0;}}
}}
</style>
<div class="spx-vol-card">
  <div class="spx-vol-grid">
    <div class="spx-vol-kpi">
      <div class="label">Range Daily precedente</div>
      <div class="number">{escape(daily_value)}</div>
      <div class="range-direction">{escape(range_comparison)}</div>
    </div>
    <div class="spx-vol-kpi">
      <div class="label">Regime VX precedente</div>
      <div class="number">{escape(vx_value)}</div>
    </div>
    <div class="spx-vol-kpi">
      <div class="label">Buffer standard</div>
      <div class="number buffer">{escape(value)}</div>
    </div>
  </div>
  <div class="spx-vol-foot">
    {detail} · Daily ES Massive {escape(daily.get('ticker','N/D'))}:
    {escape(daily_date)} · VX Cboe: {escape(vx_date)}.
    Range Daily: (High−Low)/trade Close della sessione precedente; soglia 1%.
    Buffer 15 punti se ES Close/Close &lt; −1% o VX in backwardation; altrimenti 10 se entrambe le fonti sono valide.
    Range Daily: espansione/compressione rispetto alla sessione ancora precedente, senza influenzare il buffer.
    Non è il settlement. La scadenza ES deve corrispondere a quella del grafico ES1!.
    Nessun dato della seduta analizzata.
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

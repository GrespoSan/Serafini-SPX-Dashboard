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
    """Solo contributo VX: Backwardation 15, Contango 10 punti.

    La percentuale deriva dalla curva futures VX mensili (M2/M1 - 1)*100.
    A zero esatto si segue la classificazione non-negativa della dashboard.
    """
    pct = float(contango_pct)
    if not math.isfinite(pct):
        raise ValueError("Contango VX non valido")
    return 15 if pct < 0 else 10


def combined_breakout_buffer(contango_pct=None, daily_range_pct=None, daily_candle_red=None):
    """Buffer ES secondo la regola del contesto Serafini.

    15 punti se VX mensile in backwardation OPPURE se la candela Daily ES
    precedente e' rossa (Close < Open) e il range (High-Low)/Close supera 1%.
    Close/Close e' solo una misura descrittiva e non decide il buffer.
    Se i dati non escludono entrambe le condizioni non forzare 10 punti.
    """
    def finite_or_none(value):
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Dato di volatilità non finito")
        return number

    pct = finite_or_none(contango_pct)
    daily_range = finite_or_none(daily_range_pct)
    if daily_range is not None and daily_range < 0:
        raise ValueError("Range Daily ES negativo")
    if daily_candle_red is not None and not isinstance(daily_candle_red, bool):
        raise TypeError("Colore candela Daily ES non valido")

    reasons = []
    if pct is not None and pct < 0:
        reasons.append("Backwardation VX")
    if daily_range is not None and daily_candle_red is True and daily_range > 1.0:
        reasons.append("Candela Daily ES rossa con range High-Low oltre 1%")
    if reasons:
        buffer, status = 15, "DETERMINATO"
    elif pct is not None and daily_range is not None and daily_candle_red is not None:
        buffer, status = 10, "DETERMINATO"
        reasons.append("VX non in backwardation e nessuna candela rossa con range oltre 1%")
    else:
        buffer, status = None, "NON DETERMINABILE"
        reasons.append("Dati insufficienti per escludere backwardation o candela rossa con range oltre 1%")

    return {
        "status": status,
        "buffer_es_points": buffer,
        "reason": "; ".join(reasons),
        "rule": "15 se VX in backwardation OPPURE candela Daily ES precedente rossa (Close < Open) con (High-Low)/Close > 1%; altrimenti 10 se entrambe le condizioni sono verificabili ed escluse",
        "inputs": {"vx_contango_pct": pct,
                   "previous_es_daily_range_pct": daily_range,
                   "previous_es_daily_candle_red": daily_candle_red},
        "note": ("Buffer standard indicativo secondo il chiarimento del metodo Serafini; "
                 "non è una regola universale né un segnale di ingresso."),
    }


def volatility_context(target_day):
    """Unica fonte per widget e report: non alterare i calcoli NET GAMMA."""
    from daily_volatility import previous_es_daily_range

    vx = {"status": "NON DISPONIBILE"}
    daily = {"status": "NON DISPONIBILE"}
    pct = daily_range = None
    daily_candle_red = None
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
        if daily.get("status") == "VALIDO":
            daily_range = daily.get("range_pct")
            if daily.get("open") is not None and daily.get("close") is not None:
                daily_candle_red = float(daily["close"]) < float(daily["open"])
    except Exception as exc:
        daily = {"status": "NON DISPONIBILE", "reason": type(exc).__name__,
                 "source": "Massive Futures REST · ES 1session"}
    decision = combined_breakout_buffer(pct, daily_range, daily_candle_red)
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
        '<div style="font-size:1.12rem;line-height:1.5;padding:0 0 12px;'
        'color:#dbe2eb;font-weight:600">'
        'Variazione chiusura della seduta: '
        f'<span style="color:{color};font-size:1.2rem;font-weight:800">{value}</span></div>'
    )


def render(target_day, return_context=False):
    """Riquadro compatto come riferimento grafico condiviso, VX+Daily."""
    from html import escape

    ctx = volatility_context(target_day)
    vx, daily, decision = (ctx["vix_curve"], ctx["previous_daily_range"],
                           ctx["breakout_buffer"])
    vx_ok = vx.get("status") == "VALIDO"
    daily_ok = daily.get("status") == "VALIDO"
    pct = vx.get("contango_pct") if vx_ok else None
    # VIX spot is the INDEX close, not the VX futures curve. Read-only,
    # optional, never fed into combined_breakout_buffer.
    try:
        from vix_spot_history import previous_vix_spot
        spot = previous_vix_spot(
            target_day, expected_es_day=daily.get("day") if daily_ok else None)
        spot_close = float(spot["close"])
        if not math.isfinite(spot_close) or spot_close <= 0:
            raise ValueError("VIX spot non valido.")
        spot_value = f"{spot_close:.2f}".replace(".", ",")
        spot_date = date.fromisoformat(spot["day"]).strftime("%d/%m/%Y")
        spot_note = f"Chiusura {spot_date} · Cboe"
        # Compare only two confirmed consecutive Cboe trading records.
        # Red: higher implied-volatility index. Green: lower index.
        previous = spot.get("previous_close")
        previous_day = spot.get("previous_day")
        difference = spot.get("change_points")
        percent_change = spot.get("change_pct")
        if all(v is not None for v in (
                previous, previous_day, difference, percent_change)):
            prev = float(previous)
            delta = float(difference)
            delta_pct = float(percent_change)
            previous_date = date.fromisoformat(previous_day)
            if (not all(math.isfinite(v) for v in (prev, delta, delta_pct))
                    or prev <= 0 or previous_date >= date.fromisoformat(spot["day"])):
                raise ValueError("Confronto VIX non valido.")
            previous_value = f"Seduta prima {previous_date:%d/%m/%Y}"
            previous_close_value = f"{prev:.2f}".replace(".", ",")
            change_value = f"({delta:+.2f} pt)".replace(".", ",")
            change_color = ("#ff676d" if delta > 0 else
                            "#56d49e" if delta < 0 else "#b6bcc5")
        else:
            previous_value = "Seduta prima: N/D"
            previous_close_value = "N/D"
            change_value = "N/D"
            change_color = "#b6bcc5"
    except (ValueError, TypeError, KeyError, OSError, TimeoutError,
            ImportError, UnicodeError):
        spot = {"status":"NON DISPONIBILE"}
        spot_value = "N/D"
        spot_note = "Chiusura Cboe non disponibile"
        previous_value = "Seduta prima: N/D"
        change_value = "Variazione: N/D"
        change_color = "#b6bcc5"
    # The previous close is neutral white; ONLY the bracketed point delta
    # inherits the red/green variation colour.
    previous_details_html = escape(previous_close_value)
    if change_value != "N/D":
        previous_details_html += (
            ' · <span class="vix-spot-change">'
            + escape(change_value) + '</span>'
        )
    ctx["vix_spot"] = spot
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
    # Use the same actual Open/Close as the buffer rule, never the signed
    # Close/Close movement, to describe the prior candle's colour.
    candle_label = "N/D"
    candle_text_color = "#b6bcc5"
    if daily_ok and daily.get("open") is not None and daily.get("close") is not None:
        opening, closing = float(daily["open"]), float(daily["close"])
        if closing < opening:
            candle_label, candle_text_color = "ROSSA", "#ff676d"
        elif closing > opening:
            candle_label, candle_text_color = "VERDE", "#56d49e"
        else:
            candle_label = "DOJI"
    # Close/Close is informational and is NOT used to choose the buffer.
    close_change = daily.get("change_pct") if daily_ok else None
    # Both figures come from one Massive bar set; no second source or call.
    st.markdown(signed_daily_heading(close_change), unsafe_allow_html=True)
    if vx_ok:
        regime = vx["regime"].capitalize()
        vx_value = f"{regime} ({pct:+.2f}%)".replace(".", ",")
        vx_date = vx.get("day", "—")
    else:
        vx_value, vx_date = "Non disponibile", "—"

    points = decision["buffer_es_points"]
    is_high = points == 15
    value = (
        "10 punti (bassa volatilità)" if points == 10 else
        "15 punti (alta volatilità)" if points == 15 else
        "Da determinare"
    )
    highlight = "#ff676d" if is_high else ("#56d49e" if points == 10 else "#e5b65c")
    detail = escape(decision["reason"])
    card = f"""
<style>
.spx-vol-card {{
  border:1px solid #34363c; border-radius:13px; padding:14px 18px;
  background:#090a0c; color:#f4f5f6; font-family:inherit;
}}
.spx-vol-grid {{
  display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
  gap:0; align-items:center;
}}
.spx-vol-kpi {{padding:5px 18px; min-width:0; border-left:1px solid #292b30;}}
.spx-vol-kpi:first-child {{padding-left:0; border-left:0;}}
.spx-vol-kpi .label {{font-size:.82rem; color:#b6bcc5; margin-bottom:5px;}}
.spx-vol-kpi .number {{font-size:1.15rem; font-weight:740; overflow-wrap:anywhere;}}
.spx-vol-kpi .range-direction {{font-size:.79rem;color:#abb6c4;margin-top:8px;line-height:1.5;}}
.spx-vol-kpi .candle-direction {{font-size:.84rem;font-weight:700;margin-top:7px;line-height:1.4;color:{candle_text_color};}}
.spx-vol-kpi .buffer {{color:{highlight}; font-size:1.3rem;}}
.spx-vol-kpi .vix-spot-note {{font-size:.78rem;color:#abb6c4;margin-top:7px;}}
.spx-vol-kpi .vix-spot-prior {{font-size:.78rem;color:#b6bcc5;margin-top:5px;}}
.spx-vol-kpi .vix-spot-previous-detail {{font-size:.90rem;color:#f4f5f6;font-weight:700;margin-top:5px;}}
.spx-vol-kpi .vix-spot-change {{color:{change_color};}}
.spx-vol-foot {{
  border-top:1px solid #292b30; padding-top:9px; margin-top:12px;
  color:#949ba6; font-size:.74rem; line-height:1.4;
}}
@media(max-width:950px) {{
  .spx-vol-grid {{grid-template-columns:repeat(2,minmax(0,1fr));}}
  .spx-vol-kpi:nth-child(3) {{border-left:0;padding-left:0;}}
  .spx-vol-kpi:nth-child(n+3) {{border-top:1px solid #292b30;margin-top:9px;padding-top:12px;}}
}}
@media(max-width:700px) {{
  .spx-vol-grid {{grid-template-columns:1fr;}}
  .spx-vol-kpi {{padding:10px 0!important;border-left:0;border-top:1px solid #292b30;margin-top:0!important;}}
  .spx-vol-kpi:first-child {{border-top:0;}}
}}
</style>
<div class="spx-vol-card">
  <div class="spx-vol-grid">
    <div class="spx-vol-kpi">
      <div class="label">Range Daily precedente</div>
      <div class="number">{escape(daily_value)}</div>
      <div class="range-direction">{escape(range_comparison)}</div>
      <div class="candle-direction">Candela Daily: {escape(candle_label)}</div>
    </div>
    <div class="spx-vol-kpi">
      <div class="label">Regime VX precedente</div>
      <div class="number">{escape(vx_value)}</div>
    </div>
    <div class="spx-vol-kpi">
      <div class="label">Buffer standard</div>
      <div class="number buffer">{escape(value)}</div>
    </div>
    <div class="spx-vol-kpi">
      <div class="label">VIX spot · chiusura precedente</div>
      <div class="number">{escape(spot_value)}</div>
      <div class="vix-spot-note">{escape(spot_note)}</div>
      <div class="vix-spot-prior">{escape(previous_value)}</div>
      <div class="vix-spot-previous-detail">{previous_details_html}</div>
    </div>
  </div>
  <div class="spx-vol-foot">
    Motivo del buffer: {detail}.<span class="spx-vol-source">
    ES {escape(daily.get('ticker', 'N/D'))} · {escape(daily_date)} · VX Cboe {escape(vx_date)}.</span>
  </div>
</div>"""
    st.markdown(card, unsafe_allow_html=True)
    with st.expander("Dettaglio fonti e formule", expanded=False):
        if daily_ok and daily.get("previous_close") and daily.get("previous_close_day"):
            st.caption(
                f"Massive Futures {daily['ticker']} · 1session · "
                f"chiusura {daily['day']}: {daily['close']:,.2f} / "
                f"chiusura {daily['previous_close_day']}: {daily['previous_close']:,.2f}. "
                "Close/Close sul medesimo contratto, non settlement; "
                "non determina il buffer. "
                "Verificare la scadenza rispetto a ES1!."
            )
        if daily_ok:
            opening = daily.get("open")
            close = daily.get("close")
            if opening is not None and close is not None:
                st.caption(
                    f"Candela Daily ES precedente: Open {float(opening):,.2f}; "
                    f"Close {float(close):,.2f}; {candle_label}; "
                    f"range High-Low {float(daily['range_pct']):.2f}%.")
            else:
                st.caption("Open/Close ES mancanti: colore della candela non verificabile.")
        st.caption("Range Daily ES (%) = (High − Low) / Close × 100. "
                   "Espansione/compressione: confronto con il range percentuale "
                   "della seduta ancora precedente.")
        st.caption("Fonte VIX spot: Cboe VIX Index Historical Data, CLOSE "
                   "delle ultime due sedute VIX effettive precedenti, "
                   "con differenza in punti VIX e percentuale sulla chiusura "
                   "ancora precedente. Rosso = VIX salito, verde = VIX sceso. "
                   "È volatilità implicita, non volatilità realizzata ES; "
                   "non è il future VX, non è un valore live e non modifica il buffer.")
        st.caption("Fonte VX: settlement futures mensili Cboe. "
                   "Buffer ES 15 punti in backwardation oppure con candela ES rossa "
                   "e range High-Low strettamente maggiore di 1%. "
                   "In assenza delle condizioni, 10 punti solo con dati verificati. "
                   "La variazione Close/Close resta informativa.")
        st.caption("Bassa/alta volatilità è l'etichetta del buffer standard "
                   "da 10/15 punti, non una classificazione generale del mercato.")
    if not vx_ok or not daily_ok:
        unavailable = []
        if not daily_ok:
            unavailable.append(f"Daily ES ({daily.get('reason', 'fonte indisponibile')})")
        if not vx_ok:
            unavailable.append(f"VX ({vx.get('reason', 'fonte indisponibile')})")
        st.caption("Fonte non disponibile: " + ", ".join(unavailable) +
                   ". Il buffer non viene forzato a 10 punti.")
    return ctx if return_context else pct

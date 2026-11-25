"""GEX Edge-style autonomo per CSV Cboe, matematica originale del motore Edge.

Sorgente del modello puro: GrespoSan/SPX-Edge-GEX-Lab/gexlab/engine/gex.py
Modello: gamma BSM r=q=0, Call + / Put -, OI * 100 * S^2 * 0.01.
Il CSV NON e' un feed Alpaca/OPRA: OI date e quote clock non certificati.
Il modulo non usa rete, secrets, storage o servizio GEX remoto.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
from collections import Counter
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

from csv_gex_cboe_import import csv_to_snapshot, _get_csv_fields, InvalidCboeCSV
from csv_gex_core import gex, config
from csv_gex_core.calendar import Session, TradingCalendar, NY
from csv_gex_core.models import Contract, parse_occ
from csv_gex_oi import apply_mode
from volatility_levels import weekly_expiry

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
INDEX_AM_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX"})
INDEX_PM_ROOTS = frozenset({"SPXW", "NDXP", "NDXW", "RUTW", "DJXW"})
EMPTY = frozenset({"", "-", "--", "—", "N/A"})
GEX_SOURCE = "CBOE_CSV_MANUAL_USER_EXPORT"
MODEL_LABEL = "Edge GEX 1.1 (BSM+OI, Call+/Put−) · diagnostico"


def _raw_field_presence(blob: bytes, accepted: set[str]) -> dict[str, dict[str, bool]]:
    """Distingue gli OI/IV assenti da zero, che il parser NET GAMMA normalizza."""
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("cp1252")
    rows = list(csv.reader(io.StringIO(text)))
    start, _, columns = _get_csv_fields(rows)
    presence = {}
    for row in rows[start + 1:]:
        for side in ("call", "put"):
            col = columns[side]
            idx = col["code"]
            if idx >= len(row):
                continue
            symbol = row[idx].strip().replace(" ", "").lstrip("^").upper()
            if symbol not in accepted:
                continue
            oi_idx = col["oi"]
            iv_idx = col.get("iv")
            presence[symbol] = {
                "oi": oi_idx < len(row) and row[oi_idx].strip().upper() not in EMPTY,
                "iv": iv_idx is not None and iv_idx < len(row)
                      and row[iv_idx].strip().upper() not in EMPTY,
            }
    return presence


def _iv_normalization(options: list[dict], present: dict, mode: str, *,
                      spot: float | None = None, captured: datetime | None = None,
                      cal: TradingCalendar | None = None,
                      target_expiries: dict | None = None) -> tuple[str | None, str]:
    """Rileva il FORMATO della IV senza far dipendere la scelta da ogni outlier.

    In una catena Cboe completa (anche >30.000 contratti) la IV degli strike
    remoti puo' superare 100% o essere quasi zero. Il controllo precedente
    richiedeva che TUTTI i valori fossero [0.01,1] o [10,500] e bloccava
    l'intero GEX per un singolo outlier.

    Questa versione usa solo contratti vicini allo spot, delle scadenze in
    esame, con OI > 0, e sceglie l'unita' solo quando una convenzione ha
    evidenza netta. I valori estremi non vengono corretti automaticamente:
    rimangono esclusi dalle verifiche di copertura GEX.
    """
    if mode not in {"auto", "decimal", "percent"}:
        raise ValueError("Formato IV sconosciuto.")
    if mode == "decimal":
        return "decimal", "Unità IV dichiarata manualmente: decimale."
    if mode == "percent":
        return "percent", "Unità IV dichiarata manualmente: percentuale."

    present_iv = [
        o for o in options
        if present.get(o.get("option"), {}).get("iv")
        and isinstance(o.get("iv"), (float, int))
        and math.isfinite(float(o["iv"])) and float(o["iv"]) > 0
    ]
    if not present_iv:
        return None, "Il CSV non contiene valori IV positivi utilizzabili."

    sample = []
    target_dates = set(target_expiries.values()) if target_expiries else None
    if spot is not None and math.isfinite(float(spot)) and float(spot) > 0:
        for o in present_iv:
            if not o.get("open_interest") or o["open_interest"] <= 0:
                continue
            try:
                occ = parse_occ(str(o["option"]).strip().replace(" ", ""))
            except (ValueError, TypeError):
                continue
            if target_dates is not None and occ.expiry not in target_dates:
                continue
            if abs(occ.strike / float(spot) - 1.0) > .10:
                continue
            sample.append((o, occ))
    if len(sample) < 20:
        return None, (
            f"Unità IV non determinabile: solo {len(sample)} contratti "
            "liquidi con IV nelle scadenze e nella banda ±10% spot. "
            "Nessuna unità presunta."
        )

    values = [float(o["iv"]) for o, _ in sample]
    decimal_evidence = sum(.01 <= v <= 1.0 for v in values)
    percent_evidence = sum(10.0 <= v <= 500.0 for v in values)
    decisive = decimal_evidence + percent_evidence
    count = len(values)
    # Il supporto della convenzione deve essere schiacciante. Non confondere
    # valori IV molto alti (2.0 = 200%) con IV espressa in percentuale.
    if decisive >= 20:
        if decimal_evidence / decisive >= .98 and decimal_evidence / count >= .60:
            return ("decimal",
                    f"IV riconosciuta automaticamente in decimali: "
                    f"{decimal_evidence}/{count} contratti vicini allo spot "
                    f"nelle scadenze analizzate; outlier esclusi dai soli "
                    f"controlli di unità, non dai quality gate.")
        if percent_evidence / decisive >= .98 and percent_evidence / count >= .60:
            return ("percent",
                    f"IV riconosciuta automaticamente in percentuale: "
                    f"{percent_evidence}/{count} contratti vicini allo spot "
                    f"nelle scadenze analizzate; outlier sottoposti ai quality gate.")

    if decimal_evidence >= 10 and percent_evidence >= 10:
        return None, (
            f"IV potenzialmente mista nelle scadenze esaminate: "
            f"{decimal_evidence} valori decimali e {percent_evidence} "
            "percentuali. Nessuna conversione automatica."
        )

    # Caso borderline (IV tra 1 e 10): confronto incrociato con il Gamma
    # Cboe dello stesso contratto, solo vicino allo spot. Serve una prova
    # forte sia dell'errore assoluto sia del vantaggio su unita' alternativa.
    if captured is not None and cal is not None and spot is not None:
        from csv_gex_core import bsm
        from csv_gex_core.calendar import time_to_expiry
        scores = {"decimal": [], "percent": []}
        for option, occ in sample:
            if abs(occ.strike / float(spot) - 1.0) > .015:
                continue
            observed = option.get("gamma")
            if (not isinstance(observed, (int, float)) or
                    not math.isfinite(float(observed)) or observed <= 1e-7):
                continue
            T = time_to_expiry(
                captured, occ.expiry,
                "AM" if occ.root in INDEX_AM_ROOTS else "PM", cal
            )
            if T <= 0:
                continue
            for unit, divisor in (("decimal", 1.0), ("percent", 100.0)):
                iv = float(option["iv"]) / divisor
                if not gex.iv_ok(iv):
                    continue
                theoretical = bsm.gamma(float(spot), occ.strike, T, 0.0, iv)
                if theoretical > 1e-10 and math.isfinite(theoretical):
                    scores[unit].append(abs(math.log(theoretical / float(observed))))
        if min(len(scores["decimal"]), len(scores["percent"])) >= 12:
            errors = {k: sorted(v)[len(v) // 2] for k, v in scores.items()}
            best = min(errors, key=errors.get)
            other = "percent" if best == "decimal" else "decimal"
            if errors[best] < math.log(1.6) and (
                    errors[other] - errors[best] > math.log(3.0)):
                return best, (
                    "IV riconosciuta tramite controllo incrociato BSM/Gamma Cboe "
                    f"su {len(scores[best])} contratti vicini allo spot. "
                    "Corrispondenza diagnostica, non certificazione dei prezzi."
                )
    return None, (
        f"Unità IV realmente ambigua: nel campione vicino allo spot "
        f"{decimal_evidence} decimali, {percent_evidence} percentuali, "
        f"{count - decisive} valori intermedi/estremi. "
        "Selezionare manualmente solo dopo verifica del formato CSV."
    )

def _calendar(captured: datetime) -> TradingCalendar:
    """Calendario NYSE per chiusure e festivi; proxy dichiarato per Cboe options."""
    import exchange_calendars as xcals
    nyday = captured.astimezone(NY).date()
    begin, end = nyday - timedelta(days=10), nyday + timedelta(days=30)
    exchange = xcals.get_calendar("XNYS", start=begin, end=end)
    sessions = {}
    for label, row in exchange.schedule.iterrows():
        d = label.date()
        sessions[d] = Session(
            d, row["open"].tz_convert(NY).time(),
            row["close"].tz_convert(NY).time(),
        )
    if not sessions:
        raise ValueError("Calendario XNYS vuoto: scadenze non determinabili.")
    return TradingCalendar(sessions, "exchange_calendars:XNYS", (begin, end))


def _target_expiries(captured: datetime, cal: TradingCalendar) -> dict[str, date]:
    """0DTE = prossima sessione non conclusa; Weekly = venerdi' secondo Serafini."""
    if captured.tzinfo is None:
        raise ValueError("Timestamp di acquisizione senza fuso.")
    candidates = sorted(d for d in cal.sessions if cal.close_dt(d) > captured
                        and d >= captured.astimezone(NY).date())
    if not candidates:
        raise ValueError("Nessuna sessione USA successiva documentata.")
    day = candidates[0]
    return {"0DTE": day, "Weekly": weekly_expiry(day)}


def _from_snapshot(doc: dict, presence: dict, unit: str | None) -> list[Contract]:
    """Preserva contratti OCC. Non inventa IV e OI mancanti."""
    out = []
    seen = set()
    for option in doc["data"]["options"]:
        name = str(option["option"]).strip().replace(" ", "")
        if name in seen:
            raise ValueError("OCC duplicato nel CSV.")
        seen.add(name)
        occ = parse_occ(name)
        oi = option.get("open_interest")
        reported = presence.get(name, {})
        if not reported.get("oi"):
            oi = None
        elif (not isinstance(oi, (int, float)) or not math.isfinite(float(oi))
              or oi < 0 or int(oi) != oi):
            raise ValueError("Open Interest OCC non intero o non valido.")
        else:
            oi = int(oi)
        raw_iv = option.get("iv")
        iv = None
        if unit is not None and reported.get("iv") and raw_iv is not None:
            v = float(raw_iv)
            v = v / 100.0 if unit == "percent" else v
            if math.isfinite(v) and gex.iv_ok(v):
                iv = v
        settlement = "AM" if occ.root in INDEX_AM_ROOTS else "PM"
        out.append(Contract(
            symbol=name, root=occ.root, expiry=occ.expiry,
            settlement=settlement, strike=occ.strike,
            type=occ.type, multiplier=100, oi=oi, iv=iv,
            oi_source="cboe_csv", iv_source="cboe_csv" if iv is not None else None,
            oi_date=None, quote_ts=None, iv_ts=None,
        ))
    return out


def _report(expiry: date, name: str, prepared: list[Contract],
            spot: float, target: str, status_hint: str) -> dict:
    chosen = [c for c in prepared if c.expiry == expiry]
    valid = [c for c in chosen if c.valid and c.oi is not None and c.oi > 0]
    basic = {
        "mode": name, "expiry": expiry.isoformat(), "status": "INSUFFICIENTE",
        "reason": "", "source": GEX_SOURCE, "quality_certified": False,
        "call_wall": None, "put_wall": None, "flip": None, "regime": None,
        "net_gex": None, "gross_gex": None,
        "call_gex": None, "put_gex": None, "rows": [], "curve": [],
        "contracts": len(chosen), "valid": len(valid),
        "quality_ratio": 0.0, "gex_coverage": None,
    }
    if not chosen:
        basic["reason"] = f"Scadenza {expiry:%d/%m/%Y} assente dal CSV, nessun livello ipotizzato."
        return basic
    if not any(c.type == "call" for c in chosen) or not any(c.type == "put" for c in chosen):
        basic["reason"] = "Una delle due componenti Call/Put manca."
        return basic
    cov = gex.coverage(chosen, spot)
    gcov = gex.gex_cov(chosen, spot)
    basic["quality_ratio"] = cov.ratio
    basic["gex_coverage"] = gcov.ratio
    distinct = len({c.strike for c in valid})
    valid_call = sum(c.type == "call" for c in valid)
    valid_put = sum(c.type == "put" for c in valid)
    # Gate coerente con l'originale gexlab/engine/gex.py (Edge 1.2+):
    # coverage_ok (percentuale di contratti) e' SOLO un diagnostico,
    # mentre gex_gate_ok usa il rapporto GEX NON ARROTONDATO.
    # Non impostiamo OI freshness come certificata: nel CSV la data OI manca.
    # I limiti minimi di campione e entrambi i lati sono specifici del CSV.
    eligible = (
        cov.eligible >= 20
        and gex.gex_gate_ok(gcov)
        and distinct >= 20 and valid_call >= 10 and valid_put >= 10
    )
    contract_pct = 100.0 * cov.ratio
    gex_pct = (f"{100.0 * gcov.ratio:.3f}%"
               if gcov.ratio is not None else "N/D")
    if not eligible:
        basic["reason"] = (
            f"Gate GEX insufficiente o indeterminato: "
            f"copertura stimata GEX ±5% {gex_pct} (minimo 98% non arrotondato); "
            f"copertura contratti {cov.valid}/{cov.eligible} = {contract_pct:.1f}% "
            f"(solo diagnostica), strike distinti validi {distinct}, "
            f"Call {valid_call}, Put {valid_put}. "
            "Richiesti >=20 contratti eleggibili, >=20 strike distinti "
            "e >=10 contratti validi per lato."
        )
        return basic
    levels, _, _ = gex.compute_levels(target, chosen, spot)
    curve = gex.profile_curve(chosen, spot, points=201)
    rows = gex.strike_rows(chosen, spot * .90, spot * 1.10)
    net = math.fsum(c.gex for c in valid)
    gross = math.fsum(abs(c.gex) for c in valid)
    calls = math.fsum(c.gex for c in valid if c.type == "call")
    puts = math.fsum(c.gex for c in valid if c.type == "put")
    if not math.isclose(net, calls + puts, rel_tol=1e-9, abs_tol=.01):
        basic["reason"] = "Riconciliazione NET GEX Call+Put fallita."
        return basic
    basic.update({
        "status": "DIAGNOSTICO",
        "reason": (
            f"{status_hint} Copertura GEX ±5% {gex_pct}; "
            f"contratti {cov.valid}/{cov.eligible} = {contract_pct:.1f}% "
            "(indicatore diagnostico non bloccante)."
        ),
        "call_wall": levels.call_wall, "put_wall": levels.put_wall,
        "flip": levels.nearest_zero_gamma, "regime": levels.regime,
        "net_gex": net, "gross_gex": gross,
        "call_gex": calls, "put_gex": puts,
        "rows": rows, "curve": curve,
    })
    return basic


def _premarket_spx_spot(options, expiry):
    """Near-ATM SPXW put-call parity, r=q=0 as in the GEX model.
    No quote timestamps per leg in the CSV: diagnostic estimate, not live spot.
    """
    from statistics import median
    pairs = {}
    for option in options:
        occ = parse_occ(str(option["option"]))
        if occ.root != "SPXW" or occ.expiry != expiry:
            continue
        bid, ask = float(option.get("bid") or 0), float(option.get("ask") or 0)
        if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
            continue
        pair = pairs.setdefault(occ.strike, {})
        if occ.type in pair:
            raise ValueError("Parità SPXW: contratto duplicato.")
        pair[occ.type] = (bid, ask)
    estimates = []
    for strike, pair in pairs.items():
        if set(pair) != {"call", "put"}:
            continue
        cb, ca = pair["call"]
        pb, pa = pair["put"]
        low, high = strike + cb - pa, strike + ca - pb
        mid = (low + high) / 2
        if mid > 0 and high-low <= 4.0:
            estimates.append((strike, mid, low, high))
    if len(estimates) < 10:
        raise ValueError("SPX implicito N/D: meno di 10 coppie Call/Put con spread combinato <= 4 punti.")
    initial = median(x[1] for x in estimates)
    near = [x for x in estimates if abs(x[0]-initial) <= .015*initial]
    if len(near) < 10:
        raise ValueError("SPX implicito N/D: campione vicino ATM insufficiente.")
    center = median(x[1] for x in near)
    good = [x for x in near if abs(x[1]-center) <= 2.0 and x[2]-1 <= center <= x[3]+1]
    if (len(good) < 10 or len(good)/len(near) < .80
            or sum(x[0]<center for x in good)<3
            or sum(x[0]>center for x in good)<3):
        raise ValueError("SPX implicito N/D: parità Call/Put incoerente o copertura ATM insufficiente.")
    spot = float(median(x[1] for x in good))
    return spot, {"method":"SPXW_0DTE_PUT_CALL_PARITY_R0_Q0",
                  "expiry":expiry.isoformat(),"pairs":len(good),
                  "near_pairs":len(near),"median_spread":median(x[3]-x[2] for x in good),
                  "min_implied":min(x[1] for x in good),
                  "max_implied":max(x[1] for x in good),
                  "certified":False}


def analyze_csv_gex(blob: bytes, symbol: str, iv_mode="auto", *,
                    oi_mode="csv", occ_values=None, occ_provenance=None,
                    occ_error=None) -> dict:
    """Motore GEX locale: CSV immutabile, OI OCC opzionale e tracciato."""
    if symbol is None or not isinstance(symbol, str):
        raise ValueError("Ticker CSV mancante.")
    doc = csv_to_snapshot(blob, symbol)
    provenance = doc.get("_import_provenance") or {}
    if provenance.get("source") != GEX_SOURCE or provenance.get("timestamp_utc_converted") is not True:
        raise ValueError("Non e' un CSV originale con timestamp sorgente verificato.")
    captured = datetime.strptime(
        doc["timestamp"], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=UTC)
    if captured > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("CSV con data futura: fotografia non verificabile.")
    spot = float(doc["data"]["current_price"])
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("Prezzo Cboe mancante o non valido.")
    accepted = {str(o["option"]) for o in doc["data"]["options"]}
    presence = _raw_field_presence(blob, accepted)
    cal = _calendar(captured)
    target = _target_expiries(captured, cal)
    csv_last = spot
    spot_info = {"method":"CSV_LAST","certified":False}
    ny_stamp = captured.astimezone(NY)
    session = cal.sessions.get(ny_stamp.date())
    before_open = (ny_stamp.time() < session.open if session else True)
    if symbol.upper() == "SPX" and before_open:
        if target["0DTE"] != captured.astimezone(NY).date():
            raise ValueError("GEX SPX pre-USA N/D: nessuna scadenza 0DTE della seduta corrente.")
        spot, spot_info = _premarket_spx_spot(doc["data"]["options"], target["0DTE"])
    unit, unit_note = _iv_normalization(
        doc["data"]["options"], presence, iv_mode,
        spot=spot, captured=captured, cal=cal, target_expiries=target,
    )
    contract_list = _from_snapshot(doc, presence, unit)
    contract_list, oi_info = apply_mode(
        contract_list, mode=oi_mode, captured=captured,
        target_expiries=target, spot=spot,
        occ=occ_values, provenance=occ_provenance, fetch_error=occ_error,
    )
    prepared = gex.finalize(contract_list, spot, captured, cal)
    reports = {}
    for name, expiry in target.items():
        reports[name] = _report(
            expiry, name, prepared, spot, symbol,
            (("OI OCC Series Search: data OI non pubblicata; "
              if oi_info["effective_source"] == "OCC_SERIES_SEARCH"
              else "OI del CSV senza data certificata; ") +
             "IV/quotazioni del CSV non sincronizzate con feed Alpaca. "
             "Risultato NON certificato, solo studio."),
        )
    if unit is None:
        for report in reports.values():
            report.update(
                status="INSUFFICIENTE", reason=unit_note, call_wall=None,
                put_wall=None, flip=None, regime=None,
                net_gex=None, gross_gex=None, call_gex=None, put_gex=None,
                curve=[], rows=[],
            )
    return {
        "source": GEX_SOURCE,
        "model": MODEL_LABEL,
        "symbol": symbol,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "timestamp_utc": captured.isoformat(),
        "timestamp_rome": captured.astimezone(ROME).strftime("%d/%m/%Y %H:%M:%S"),
        "spot": spot, "csv_last": csv_last, "spot_info": spot_info,
        "iv_unit": unit, "iv_note": unit_note,
        "oi_info": oi_info,
        "oi_missing_effective": sum(c.oi is None for c in contract_list),
        "csv_contracts": len(contract_list), "oi_missing": sum(
            not presence.get(c.symbol, {}).get("oi") for c in contract_list
        ),
        "iv_missing": sum(
            not presence.get(c.symbol, {}).get("iv") for c in contract_list
        ),
        "reports": reports,
        "quality_certified": False,
    }

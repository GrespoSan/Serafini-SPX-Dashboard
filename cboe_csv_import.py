"""Explicit, local-only import of Cboe Quotes Dashboard SPX option-chain CSV.

The CSV is NOT the original Cboe JSON. Parse only documented data columns,
never fill an unknown spot/date/previous close with market guesses. The
converted JSON is a derived local representation for unchanged Serafini math.
"""
import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_CSV_ROWS = 60000
CODE = re.compile(r"^(SPXW?)(\d{6})([CP])(\d{8})$", re.I)
MONTHS = {
    "gennaio": "January", "febbraio": "February", "marzo": "March",
    "aprile": "April", "maggio": "May", "giugno": "June",
    "luglio": "July", "agosto": "August", "settembre": "September",
    "ottobre": "October", "novembre": "November", "dicembre": "December",
}


class InvalidCboeCSV(ValueError):
    pass


def _number(raw, label, *, empty_zero=False):
    value = str(raw or "").strip().replace(",", "").replace(" ", "")
    if not value or value in ("-", "—", "N/A", "--"):
        if empty_zero:
            return 0.0
        raise InvalidCboeCSV(f"CSV: campo {label} mancante.")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise InvalidCboeCSV(f"CSV: {label} non numerico.") from exc
    if not math.isfinite(number):
        raise InvalidCboeCSV(f"CSV: {label} non finito.")
    return number


def _source_time(text):
    """Timestamp with explicitly declared offset; normalize to UTC for reader."""
    value = str(text).strip().strip('"').strip()
    pattern = re.search(
        r"\s+(GMT|UTC)([+-]\d{1,2}(?::?\d{2})?)?\s*$",
        value, flags=re.I)
    fixed = None
    zone = None
    method = None
    if pattern:
        suffix = pattern.group(2) or ""
        value = value[:pattern.start()].strip()
        hours = minutes = 0
        if suffix:
            match = re.fullmatch(r"([+-])(\d{1,2})(?::?(\d{2}))?", suffix)
            if not match:
                raise InvalidCboeCSV("CSV: offset del timestamp non valido.")
            sign = 1 if match.group(1) == "+" else -1
            hours = sign * int(match.group(2))
            minutes = sign * int(match.group(3) or 0)
            if abs(hours) > 14 or abs(minutes) > 59:
                raise InvalidCboeCSV("CSV: offset UTC fuori intervallo.")
        fixed = timezone(timedelta(hours=hours, minutes=minutes))
        method = "UTC esplicito" if not suffix and pattern.group(1).upper() == "UTC" else "GMT/UTC offset esplicito"
    else:
        pattern = re.search(r"\s+(EST|EDT|ET|CET|CEST)\s*$", value, flags=re.I)
        if not pattern:
            raise InvalidCboeCSV(
                "CSV: data/ora senza fuso verificabile (es. GMT+1, EST, EDT). "
                "Non attribuisco automaticamente il fuso UTC.")
        suffix = pattern.group(1).upper()
        value = value[:pattern.start()].strip()
        fixed = {"EST": timezone(timedelta(hours=-5)),
                 "EDT": timezone(timedelta(hours=-4)),
                 "CET": timezone(timedelta(hours=1)),
                 "CEST": timezone(timedelta(hours=2))}.get(suffix)
        zone = ZoneInfo("America/New_York") if suffix == "ET" else None
        method = suffix + " dichiarato"
    for it, en in MONTHS.items():
        value = re.sub(r"\b" + it + r"\b", en, value, flags=re.I)
    value = re.sub(r"\s+(?:at|alle)\s+", " ", value, flags=re.I)
    value = value.replace(" a.m.", " AM").replace(" p.m.", " PM")
    formats = (
        "%B %d, %Y, %I:%M:%S %p", "%B %d, %Y, %I:%M %p",
        "%B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M %p",
        "%B %d, %Y, %H:%M:%S", "%B %d, %Y, %H:%M",
        "%B %d, %Y %H:%M:%S", "%B %d, %Y %H:%M",
        "%d %B %Y %H:%M:%S", "%d %B %Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    )
    naive = None
    for fmt in formats:
        try:
            naive = datetime.strptime(value.strip(), fmt)
            break
        except ValueError:
            continue
    if naive is None:
        raise InvalidCboeCSV(f"CSV: data e ora non interpretabili: {text[:85]}")
    aware = naive.replace(tzinfo=zone or fixed)
    if zone is not None:
        # Reject nonexistent times in NY DST transitions; never backdate a quote.
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) != naive:
            raise InvalidCboeCSV("CSV: orario ET inesistente nel cambio DST.")
    return aware.astimezone(timezone.utc), method


def _match_source_header(first):
    if not first:
        raise InvalidCboeCSV("CSV privo di intestazione SPX.")
    ticker = first[0].strip().lstrip("\ufeff").upper()
    if (not re.search(r"\(\s*\^SPX\s*\)", ticker)
            and not re.match(r"^\^?SPX(?:\s|\()", ticker)):
        raise InvalidCboeCSV("CSV: sottostante non SPX (solo SPX/SPXW).")


def _get_csv_fields(lines):
    header_at = None
    for i, row in enumerate(lines[:15]):
        cells = [c.strip().lstrip("\ufeff") for c in row]
        if ("Expiration Date" in cells and "Calls" in cells and
                "Strike" in cells and "Puts" in cells):
            header_at = i
            break
    if header_at is None:
        raise InvalidCboeCSV("CSV: tabella Cboe non riconosciuta (Expiration Date, Calls, Strike, Puts).")
    fields = [c.strip() for c in lines[header_at]]
    strike = fields.index("Strike")
    call = fields.index("Calls")
    put = fields.index("Puts")
    if not (call < strike < put):
        raise InvalidCboeCSV("CSV: disposizione Call/Strike/Put non riconosciuta.")
    def idx(label, start, end):
        found = [i for i in range(start, end) if fields[i] == label]
        if len(found) != 1:
            raise InvalidCboeCSV("CSV: colonna mancante o duplicata: " + label)
        return found[0]
    columns = {}
    for side, start, end in (("call", call, strike), ("put", put, len(fields))):
        columns[side] = {
            "code": idx("Calls" if side == "call" else "Puts", start, end),
            "gamma": idx("Gamma", start, end),
            "volume": idx("Volume", start, end),
            "oi": idx("Open Interest", start, end),
        }
        for optional in ("Delta", "IV", "Bid", "Ask"):
            matches = [i for i in range(start, end) if fields[i] == optional]
            if len(matches) == 1:
                columns[side][optional.lower()] = matches[0]
    return header_at, strike, columns


def csv_to_snapshot(payload):
    """Convert Cboe *user-downloaded* SPX CSV to an annotated local snapshot."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CSV_BYTES:
        raise InvalidCboeCSV("CSV vuoto o superiore a 25 MiB.")
    try:
        content = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = payload.decode("cp1252")
    try:
        lines = list(csv.reader(io.StringIO(content, newline="")))
    except csv.Error as exc:
        raise InvalidCboeCSV("CSV non valido.") from exc
    if not 3 <= len(lines) <= MAX_CSV_ROWS:
        raise InvalidCboeCSV("CSV vuoto o troppo esteso.")
    _match_source_header(lines[0])
    head = "\n".join(content.splitlines()[:3])
    m_last = re.search(r"\bLast\s*:\s*([+-]?[\d,]+(?:\.\d+)?)", head, re.I)
    m_change = re.search(r"\bChange\s*:\s*([+-]?[\d,]+(?:\.\d+)?)", head, re.I)
    if not (m_last and m_change):
        raise InvalidCboeCSV(
            "CSV: servono Last e Change nell'intestazione per ottenere "
            "spot e chiusura precedente, senza stime.")
    spot = _number(m_last.group(1), "Last")
    change = _number(m_change.group(1), "Change")
    if not (spot > 0 and spot - change > 0 and abs(change) < spot * .25):
        raise InvalidCboeCSV("CSV: Last/Change non coerenti.")
    date_cell = next(
        (cell.strip()[5:].strip() for row in lines[:7] for cell in row
         if cell.strip().lower().startswith("date:")), None)
    if date_cell is None:
        raise InvalidCboeCSV("CSV: manca Date nell'intestazione; nessun timestamp inventato.")
    recorded, clock_source = _source_time(date_cell)
    ny_timestamp = recorded.astimezone(ZoneInfo("America/New_York"))
    # The SPX index has no premarket cash session. Before 09:30 New York,
    # Cboe 'Last' is normally the last US cash close (yesterday's close).
    # 'Change' is the movement of that LAST cash session, so subtracting it
    # before the next open shifts the reference back by one extra session.
    # Outside US weekdays (when market cash cannot open) use the same rule.
    before_cash_open = (ny_timestamp.weekday() >= 5 or
                        (ny_timestamp.hour, ny_timestamp.minute) < (9, 30))
    if before_cash_open:
        reference = spot
        reference_method = (
            "Last Cboe pre-apertura cash USA (valore ultimo indice, "
            "verificare la chiusura precedente)")
    else:
        reference = spot - change
        reference_method = "Last meno Change Cboe (sessione cash USA)"
    header_at, strike_index, columns = _get_csv_fields(lines)
    options = []
    seen = set()
    omitted = 0
    for row in lines[header_at+1:]:
        if not any(c.strip() for c in row):
            continue
        if len(row) < max(columns["put"].values()) + 1:
            # Footer/blank line not mistaken for an options contract.
            if any("SPX" in c.upper() for c in row):
                raise InvalidCboeCSV("CSV: riga opzioni troncata.")
            continue
        row_strike = _number(row[strike_index], "Strike", empty_zero=True)
        if row_strike <= 0:
            continue
        for side, expected in (("call", "C"), ("put", "P")):
            c = columns[side]
            ident = row[c["code"]].strip().replace(" ", "").lstrip("^").upper()
            if not ident:
                continue
            match = CODE.fullmatch(ident)
            if match is None:
                # SPXO is not a supported original Serafini OCC root.
                if ident.startswith(("SPXO", "SPX")):
                    omitted += 1
                    continue
                raise InvalidCboeCSV("CSV: opzione non SPX riconosciuta: " + ident[:24])
            if match.group(3) != expected:
                raise InvalidCboeCSV("CSV: opzione Call/Put nella colonna errata.")
            actual_strike = int(match.group(4)) / 1000.0
            if not math.isclose(actual_strike, row_strike, abs_tol=.001):
                raise InvalidCboeCSV("CSV: strike opzione incoerente con la riga.")
            if ident in seen:
                raise InvalidCboeCSV("CSV: contratto duplicato " + ident)
            seen.add(ident)
            oi = _number(row[c["oi"]], "Open Interest", empty_zero=True)
            vol = _number(row[c["volume"]], "Volume", empty_zero=True)
            gam = _number(row[c["gamma"]], "Gamma", empty_zero=(oi == 0 and vol == 0))
            if min(oi, vol, gam) < 0:
                raise InvalidCboeCSV("CSV: Gamma, OI e Volume devono essere non negativi.")
            item = {"option": ident, "gamma": gam,
                    "open_interest": oi, "volume": vol}
            for extra, source in (("delta", "delta"), ("iv", "iv"),
                                  ("bid", "bid"), ("ask", "ask")):
                if source in c:
                    item[extra] = _number(row[c[source]], extra, empty_zero=True)
            options.append(item)
    if len(options) < 100 or not any(opt["gamma"] > 0 for opt in options):
        raise InvalidCboeCSV(
            "CSV: catena SPX insufficiente o senza Gamma. "
            "Seleziona Options Range = All, poi View Chain e Download CSV.")
    # Detect narrow Cboe 'Near the Money' exports before attempting 25+25.
    # Report coverage, but do not invent strikes or change Serafini's method.
    from volatility_levels import weekly_expiry
    market_day = ny_timestamp.date()
    coverage = {}
    if market_day.weekday() < 5:
        for label, expiry in (("0DTE", market_day),
                              ("Weekly", weekly_expiry(market_day))):
            price_levels = set()
            for item in options:
                match = CODE.fullmatch(item["option"])
                if match and match.group(2) == expiry.strftime("%y%m%d"):
                    price_levels.add(int(match.group(4)) / 1000.0)
            coverage[label] = {
                "expiry": expiry.isoformat(),
                "below": len([k for k in price_levels if k < reference]),
                "above": len([k for k in price_levels if k > reference]),
                "distinct": len(price_levels),
                "minimum": min(price_levels) if price_levels else None,
                "maximum": max(price_levels) if price_levels else None,
                "complete": (
                    sum(k < reference for k in price_levels) >= 25 and
                    sum(k > reference for k in price_levels) >= 25),
            }
    return {
        "symbol": "_SPX",
        # UTC convention required by the existing Pine labels; unlike Cboe
        # raw JSON, this timestamp has a confirmed timezone from the CSV.
        "timestamp": recorded.strftime("%Y-%m-%d %H:%M:%S"),
        "data": {"prev_day_close": reference,
                 "current_price": spot, "options": options},
        "_import_provenance": {
            "source": "CBOE_CSV_MANUAL_USER_EXPORT",
            "original_csv_sha256": hashlib.sha256(payload).hexdigest(),
            "original_timestamp_text": date_cell,
            "timezone_conversion": clock_source,
            "timestamp_utc_converted": True,
            "previous_close_method": reference_method,
            "prior_close_spx": reference,
            "reference_is_preopen_last": before_cash_open,
            "reference_market_day_ny": market_day.isoformat(),
            "coverage_by_expiry": coverage,
            "contracts": len(options),
            "unsupported_contracts_excluded": omitted,
            "not_original_cboe_json": True,
            "filtered_chain_possible": True,
        },
    }


def csv_coverage_warning(provenance):
    """Explain an incomplete Cboe CSV chain without filling missing strikes."""
    info = (provenance or {}).get("coverage_by_expiry") or {}
    incomplete = []
    for label in ("0DTE", "Weekly"):
        v = info.get(label)
        if not v or v.get("complete"):
            continue
        low = v.get("minimum")
        high = v.get("maximum")
        span = (f" · strike {low:,.0f}–{high:,.0f}" if
                isinstance(low, (float, int)) and isinstance(high, (float, int))
                else " · nessuno strike per la scadenza")
        incomplete.append(
            f'{label} {v.get("expiry", "?")}: '
            f'{v.get("below", 0)}/25 sotto, {v.get("above", 0)}/25 sopra'
            + span)
    if not incomplete:
        return None
    return (
        "CSV Cboe con catena INCOMPLETA per il metodo Serafini: "
        + "; ".join(incomplete)
        + ". Nel portale Cboe imposta **Options Range = All** "
          "(NON Near the Money), **Expiration = All**, poi "
          "**View Chain** e infine **Download CSV**. "
          "L'app non genera profili 25+25 da strike assenti."
    )

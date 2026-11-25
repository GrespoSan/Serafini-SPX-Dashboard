"""Pine v6: solo livelli GEX CSV modellati, senza ricalcoli.

Il NET GAMMA Serafini non e' modificato. Nessuna rete, storage o Alpaca.
"""
from __future__ import annotations

import math
import re


MODE_BY_LABEL = {
    "Rilevamento automatico (blocco se IV ambigua)": "auto",
    "IV decimale · 0,20 = 20%": "decimal",
    "IV percentuale · 20 = 20%": "percent",
}
GEX_FIELDS = (("call_wall", "Call Wall"),
              ("put_wall", "Put Wall"),
              ("flip", "Gamma Flip"))


def validate_result(saved, source, mode):
    """Verifica hash CSV, ticker, unita' IV e qualità di ciascuna scadenza."""
    if not isinstance(source, dict) or not source.get("sha256") or not source.get("symbol"):
        raise ValueError("Caricare un CSV Cboe verificato prima dell'esportazione.")
    if mode not in ("auto", "decimal", "percent"):
        raise ValueError("Selezionare e calcolare l'unità IV nel pannello GEX.")
    if not isinstance(saved, tuple) or len(saved) != 2:
        raise ValueError("Calcolare prima GEX dalla stessa fotografia CSV.")
    signature, report = saved
    # Legacy 3-part keys remain valid for existing CSV-only workflows.
    # New 4-part keys additionally bind the OCC/CSV source selection.
    oi_mode = source.get("oi_mode", "csv")
    expected = ((source["sha256"], source["symbol"], mode)
                if len(signature) == 3 and oi_mode == "csv"
                else (source["sha256"], source["symbol"], mode, oi_mode))
    if (signature != expected
            or not isinstance(report, dict)
            or report.get("sha256") != source["sha256"]
            or report.get("symbol") != source["symbol"]
            or (report.get("oi_info") or {}).get("requested_mode",oi_mode) != oi_mode):
        raise ValueError("Risultato GEX non riferito al CSV/mercato/IV corrente: ricalcolare.")
    if report.get("source") != "CBOE_CSV_MANUAL_USER_EXPORT":
        raise ValueError("GEX non proveniente da questa fotografia CSV Cboe.")
    if report.get("quality_certified") is not False:
        raise ValueError("Provenienza o qualità GEX non compatibile con il modello CSV.")
    groups = report.get("reports")
    if not isinstance(groups, dict) or set(groups) != {"0DTE", "Weekly"}:
        raise ValueError("Scadenze GEX non valide.")
    good = {name: groups[name] for name in ("0DTE", "Weekly")
            if isinstance(groups[name], dict) and groups[name].get("status") == "DIAGNOSTICO"}
    if not good:
        raise ValueError("0DTE e Weekly senza qualità diagnostica sufficiente: GEX N/D.")
    return report, good


def _finite_price(original, scale, basis):
    if original is None:
        return None
    x = float(original)
    y = x * scale + basis
    if not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0:
        raise ValueError("Livello GEX o conversione future non valida.")
    return y


def _pine_price(price):
    """Prezzo della linea presentato al centesimo, mai al tick ES/MES.

    Il GEX BSM resta intatto nel report: cambia soltanto la precisione
    grafica della proiezione Pine, coerente con il testo dell'etichetta.
    """
    return "na" if price is None else f"{price:.2f}"


def append_csv_gex(code: str, report: dict, groups: dict, *,
                   price_factor: float = 1.0, price_shift: float = 0.0) -> str:
    """Il Pine originale resta identico come prefisso; si aggiunge un overlay."""
    factor, shift = float(price_factor), float(price_shift)
    if not (math.isfinite(factor) and math.isfinite(shift) and 0 < factor < 1000):
        raise ValueError("Differenziale o rapporto ETF/future non verificato.")
    if not isinstance(code, str) or not code.startswith("//@version=6"):
        raise ValueError("Pine originale non valido.")
    if not re.search(r"\bvalidTicker\s*=", code):
        raise ValueError("Il Pine non possiede il controllo dello strumento.")
    if "// INIZIO CSV-GEX-OVERLAY" in code:
        raise ValueError("Il Pine contiene già un blocco GEX CSV.")
    if not isinstance(groups, dict) or not groups:
        raise ValueError("Non esistono scadenze GEX utilizzabili.")
    stamp = str(report.get("timestamp_rome", "sconosciuto"))
    if not re.fullmatch(r"\d\d/\d\d/\d{4} \d\d:\d\d:\d\d", stamp):
        raise ValueError("Timestamp sorgente GEX non verificabile.")
    oi_info = report.get("oi_info") or {}
    oi_source = oi_info.get("effective_source", "CSV_CBOE")
    oi_caption = ("OCC Series Search · data OI NON pubblicata"
                  if oi_source == "OCC_SERIES_SEARCH"
                  else "CSV Cboe · data OI NON documentata")
    source_sha = str(report.get("sha256", ""))
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha):
        raise ValueError("Impronta SHA-256 sorgente GEX assente.")
    lines = [
        "",
        "// INIZIO CSV-GEX-OVERLAY: GEX modellato, distinto dal NET GAMMA Serafini.",
        f"// Fonte: CBOE CSV manuale, SHA256 {source_sha[:16]}; {stamp} Europe/Rome.",
        f"// Fonte OI GEX: {oi_caption}. Non certificata; modello diagnostico.",
        "// Modello: Black-Scholes, OI (NO volume), Call + / Put -, USD per movimento spot dell'1%.",
        "// OI/IV non datati singolarmente: SOLO DIAGNOSTICO, non livelli operativi certificati.",
        "// I livelli sono STATICI alla fotografia; non vengono ricalcolati su TradingView.",
        "// I prezzi GEX sono rappresentati a due decimali, non al tick futures.",
        f"// Conversione: prezzo * {factor:.8f} + {shift:.8f}.",
        '_csvGexGroup = "GEX modellato da CSV Cboe · diagnostico"',
    ]
    for name, suffix, enabled in (("0DTE", "0", "false"), ("Weekly", "W", "false")):
        if name not in groups:
            lines.append(f"// {name}: qualità insufficiente, livelli N/D ed esclusi.")
            continue
        lines.append(f'csvGexShow{suffix} = input.bool({enabled}, "Mostra GEX {name}", group=_csvGexGroup)')
        group = groups[name]
        if group.get("quality_certified") is not False:
            raise ValueError(f"Qualità GEX {name} incoerente.")
        expiry = group.get("expiry", "")
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", str(expiry)):
            raise ValueError(f"Scadenza GEX {name} assente.")
        regime = group.get("regime")
        if regime not in ("positive", "negative", "neutral"):
            raise ValueError(f"Regime GEX {name} incoerente.")
        lines.append(f"// GEX {name} · scadenza {expiry} · regime {regime}")
        for field, label in GEX_FIELDS:
            price = _finite_price(group.get(field), factor, shift)
            var = f"_csvGex{suffix}{'Call' if field == 'call_wall' else 'Put' if field == 'put_wall' else 'Flip'}"
            lines.append(f"float {var} = {_pine_price(price)}")
    lines.extend([
        "var array<line> _csvGexDrawnLines = array.new_line()",
        "var array<label> _csvGexDrawnLabels = array.new_label()",
        "if barstate.islast",
        "    for oldLine in _csvGexDrawnLines",
        "        line.delete(oldLine)",
        "    array.clear(_csvGexDrawnLines)",
        "    for oldLabel in _csvGexDrawnLabels",
        "        label.delete(oldLabel)",
        "    array.clear(_csvGexDrawnLabels)",
        "    if validTicker",
    ])
    for name, suffix in (("0DTE", "0"), ("Weekly", "W")):
        if name not in groups:
            continue
        for field, label in GEX_FIELDS:
            value_id = f"_csvGex{suffix}{'Call' if field == 'call_wall' else 'Put' if field == 'put_wall' else 'Flip'}"
            if groups[name].get(field) is None:
                continue
            pine_color = ("color.rgb(34,197,94)" if field == "call_wall" else
                          "color.rgb(239,68,68)" if field == "put_wall" else
                          "color.rgb(234,179,8)")
            style = "line.style_solid" if name == "0DTE" else "line.style_dashed"
            caption = f"{name} GEX {label}"
            lines.extend([
                f"        if csvGexShow{suffix} and not na({value_id})",
                f"            line gl = line.new(bar_index, {value_id}, bar_index + 1, {value_id}, xloc=xloc.bar_index, extend=extend.both, color={pine_color}, style={style}, width=2)",
                "            array.push(_csvGexDrawnLines, gl)",
                # Come nel precedente overlay Edge: la label segue il bordo
                # sinistro della finestra visibile anche dopo zoom/pan.
                # Formatta ES a due decimali senza arrotondare al mintick.
                f'            label lb = label.new(chart.left_visible_bar_time, {value_id}, "{caption} " + str.tostring({value_id}, "#.00"), xloc=xloc.bar_time, yloc=yloc.price, style=label.style_label_left, color=color.new(color.black, 25), textcolor={pine_color}, size=size.normal)',
                "            array.push(_csvGexDrawnLabels, lb)",
            ])
    lines.append("// FINE CSV-GEX-OVERLAY")
    return code.rstrip() + "\n" + "\n".join(lines) + "\n"

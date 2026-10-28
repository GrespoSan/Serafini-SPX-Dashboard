"""Esporta tre profili Pine v6 DISTANZIATI: precedente opzionale, 0DTE e Weekly.

L'esportatore esistente delle 07:30 rimane intatto e identico (V49).
Non vengono ricalcolati, stimati o normalizzati valori NET GAMMA.
"""
from math import isfinite

from pine_export import _pine_array, make_pine


def make_pine_comparison(previous_odte, current_odte, current_weekly,
                         offset, date_text, previous_slot, current_slot,
                         cboe_stamp=""):
    if (previous_slot, current_slot) not in (
            ("07:30", "13:30"), ("07:30", "15:50"), ("15:50", "20:30")):
        raise ValueError("Coppia di rilevamenti Pine non prevista")
    if not isfinite(float(offset)):
        raise ValueError("Differenziale ES-SPX non valido")
    for p in (previous_odte, current_odte, current_weekly):
        rows = p.get("rows") or []
        if len(rows) != 50 or len({float(z["strike_spx"]) for z in rows}) != 50:
            raise ValueError("Necessari 50 strike reali distinti in ogni profilo")
        if not all(isfinite(float(z["net_gamma"])) for z in rows):
            raise ValueError("NET GAMMA non finito")
    if previous_odte["expiry"] != current_odte["expiry"]:
        raise ValueError("0DTE precedente e attuale: scadenze differenti")

    code = make_pine(current_odte, current_weekly, float(offset),
                     date_text, cboe_stamp, current_slot)

    def patch(before, after):
        nonlocal code
        if code.count(before) != 1:
            raise RuntimeError("Struttura esportatore Pine V49 modificata: niente export incoerente")
        code = code.replace(before, after)

    patch("// EXPORT REVISION: V49-TV-COMMON-SCALE-LABEL",
          "// EXPORT REVISION: V51-TV-SPACED-THREE-COLUMNS")
    patch("// 0DTE e Weekly usano SEMPRE la stessa scala effettiva nel medesimo snapshot.",
          "// La scala effettiva e' comune ai profili visualizzati.")
    patch('offsetOdte = input.int(60, "Distanza 0DTE da ultima candela", minval=45, maxval=170)',
          'offsetOdte = input.int(60, "Distanza 0DTE attuale (senza precedente)", minval=45, maxval=170)\n'
          'showPrevious = input.bool(true, "Mostra 0DTE precedente")\n'
          'offsetPrevious = input.int(70, "1 · Distanza 0DTE precedente", minval=40, maxval=140)\n'
          'offsetOdteCompare = input.int(235, "2 · Distanza 0DTE attuale", minval=165, maxval=300)\n'
          'offsetWeeklyCompare = input.int(400, "3 · Distanza Weekly attuale", minval=300, maxval=440)')
    patch("var array<float> odteLevels = ",
          "var array<float> prevLevels = " +
          _pine_array(previous_odte["rows"], "strike_spx", float(offset)) +
          "\nvar array<float> prevValues = " +
          _pine_array(previous_odte["rows"], "net_gamma") +
          "\nvar array<float> odteLevels = ")
    patch('string scaleText = " | scala comune " + str.tostring(actualScale, "#.######") + " Gamma/bar" + (adjusted ? " (adattata)" : "")',
          'string scaleText = adjusted ? " · scala " + str.tostring(actualScale, "#.###") : ""')
    patch("if barstate.islast\n",
          "requiredCompactScale(array<float> gamma, float room) =>\n"
          "    float maxAbsolute = 0.0\n"
          "    for g in gamma\n"
          "        maxAbsolute := math.max(maxAbsolute, math.abs(g))\n"
          "    maxAbsolute / math.max(room, 1.0)\n\n"
          "if barstate.islast\n")
    patch(
        "    float actualScale = math.max(gammaPerCandle, math.max(requiredScale(odteValues, offsetOdte), requiredScale(weeklyValues, offsetWeekly)))\n"
        "    bool adjusted = actualScale > gammaPerCandle + 0.000001\n"
        f'    renderProfile(odteLevels, odteValues, bar_index + offsetOdte, "0DTE {current_odte["expiry"]} | {current_slot}", actualScale, adjusted)\n'
        f'    renderProfile(weeklyLevels, weeklyValues, bar_index + offsetWeekly, "WEEKLY {current_weekly["expiry"]} | {current_slot}", actualScale, adjusted)',
        "    // Le distanze sono indipendenti. Se troppo vicine, i centri\n"
        "    // vengono separati automaticamente (nessun livello troncato).\n"
        "    int minSeparation = 130\n"
        "    int prevCenter = math.min(offsetPrevious, 430 - 2 * minSeparation)\n"
        "    int currentCompareCenter = math.min(math.max(offsetOdteCompare, prevCenter + minSeparation), 430 - minSeparation)\n"
        "    int weeklyCompareCenter = math.min(math.max(offsetWeeklyCompare, currentCompareCenter + minSeparation), 430)\n"
        "    int currentCenter = showPrevious ? currentCompareCenter : offsetOdte\n"
        "    int weeklyCenter = showPrevious ? weeklyCompareCenter : math.min(430, math.max(offsetWeekly, offsetOdte + 70))\n"
        "    // Barre verso destra e sinistra: almeno 22 barre vuote fra profili.\n"
        "    float gapLeft = float(currentCompareCenter - prevCenter - 22) / 2.0\n"
        "    float gapRight = float(weeklyCompareCenter - currentCompareCenter - 22) / 2.0\n"
        "    float compareRoom = math.max(1.0, math.min(math.min(gapLeft, gapRight), math.min(float(480 - weeklyCompareCenter - 10), float(prevCenter - 10))))\n"
        "    float normalRoom = math.max(1.0, math.min(float(weeklyCenter - currentCenter - 22) / 2.0, math.min(float(480 - weeklyCenter - 10), float(currentCenter - 10))))\n"
        "    float normalScale = math.max(requiredCompactScale(odteValues, normalRoom), requiredCompactScale(weeklyValues, normalRoom))\n"
        "    float compareScale = math.max(requiredCompactScale(prevValues, compareRoom), math.max(requiredCompactScale(odteValues, compareRoom), requiredCompactScale(weeklyValues, compareRoom)))\n"
        "    float actualScale = math.max(gammaPerCandle, showPrevious ? compareScale : normalScale)\n"
        "    bool adjusted = actualScale > gammaPerCandle + 0.000001\n"
        f'    string currentTitle = showPrevious ? "2 · 0DTE {current_slot}" : "0DTE {current_slot}"\n'
        f'    string weeklyTitle = showPrevious ? "3 · Weekly {current_slot}" : "Weekly {current_slot}"\n'
        "    if showPrevious\n"
        f'        renderProfile(prevLevels, prevValues, bar_index + prevCenter, "1 · 0DTE {previous_slot}", actualScale, adjusted)\n'
        f'    renderProfile(odteLevels, odteValues, bar_index + currentCenter, currentTitle, actualScale, adjusted)\n'
        f'    renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, weeklyTitle, actualScale, adjusted)'
    )
    return code

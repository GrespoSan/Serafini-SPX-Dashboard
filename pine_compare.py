"""Esporta tre profili Pine v6: 0DTE precedente opzionale, 0DTE e Weekly attuali.

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
          "// EXPORT REVISION: V50-TV-OPTIONAL-PREVIOUS-0DTE")
    patch("// 0DTE e Weekly usano SEMPRE la stessa scala effettiva nel medesimo snapshot.",
          "// La scala effettiva e' comune ai profili visualizzati.")
    patch('offsetOdte = input.int(60, "Distanza 0DTE da ultima candela", minval=45, maxval=170)',
          'offsetOdte = input.int(60, "Distanza 0DTE attuale (senza precedente)", minval=45, maxval=170)\n'
          'showPrevious = input.bool(true, "Mostra 0DTE precedente")\n'
          'offsetPrevious = input.int(55, "Distanza 0DTE precedente", minval=30, maxval=120)\n'
          'offsetOdteCompare = input.int(215, "Distanza 0DTE attuale (con precedente)", minval=170, maxval=270)\n'
          'offsetWeeklyCompare = input.int(375, "Distanza Weekly (con precedente)", minval=320, maxval=430)')
    patch("var array<float> odteLevels = ",
          "var array<float> prevLevels = " +
          _pine_array(previous_odte["rows"], "strike_spx", float(offset)) +
          "\nvar array<float> prevValues = " +
          _pine_array(previous_odte["rows"], "net_gamma") +
          "\nvar array<float> odteLevels = ")
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
        "    int currentCenter = showPrevious ? offsetOdteCompare : offsetOdte\n"
        "    int weeklyCenter = showPrevious ? offsetWeeklyCompare : offsetWeekly\n"
        "    float compactRoom = math.min(float(offsetOdteCompare - offsetPrevious) / 2.0 - 8.0, float(offsetWeeklyCompare - offsetOdteCompare) / 2.0 - 8.0)\n"
        "    compactRoom := math.min(compactRoom, float(480 - offsetWeeklyCompare))\n"
        "    float normalScale = math.max(requiredScale(odteValues, offsetOdte), requiredScale(weeklyValues, offsetWeekly))\n"
        "    float compareScale = math.max(requiredCompactScale(prevValues, compactRoom), math.max(requiredCompactScale(odteValues, compactRoom), requiredCompactScale(weeklyValues, compactRoom)))\n"
        "    float actualScale = math.max(gammaPerCandle, showPrevious ? compareScale : normalScale)\n"
        "    bool adjusted = actualScale > gammaPerCandle + 0.000001\n"
        "    if showPrevious\n"
        f'        renderProfile(prevLevels, prevValues, bar_index + offsetPrevious, "0DTE {previous_odte["expiry"]} | {previous_slot} (precedente)", actualScale, adjusted)\n'
        f'    renderProfile(odteLevels, odteValues, bar_index + currentCenter, "0DTE {current_odte["expiry"]} | {current_slot}", actualScale, adjusted)\n'
        f'    renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, "WEEKLY {current_weekly["expiry"]} | {current_slot}", actualScale, adjusted)'
    )
    return code

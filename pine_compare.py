"""Pine comparativo 3 colonne: distanze ravvicinate e scala MANUALE.

Il differenziale ES-SPX si applica solo ai prezzi. NET GAMMA inalterato.
Non alterare automaticamente scala ne' posizioni scelte dall'utente.
"""
from math import isfinite
from pine_export import _pine_array, make_pine, _initial_manual_scale


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
    # Il quarto Pine può avere gamma molto maggiori della rilevazione 15:50.
    # Il default è calcolato dai dati reali, ma resta una scala MANUALE.
    base_default = _initial_manual_scale([(current_odte, 60), (current_weekly, 160)])
    comparison_default = _initial_manual_scale([
        (previous_odte, 70), (current_odte, 165), (current_weekly, 275)
    ])
    safe_default = max(base_default, comparison_default)

    def patch(before, after):
        nonlocal code
        if code.count(before) != 1:
            raise RuntimeError(
                "Il Pine di base e' cambiato: export di confronto non sicuro")
        code = code.replace(before, after)

    patch(
        f'gammaPerCandle = input.float({base_default:.1f}, "Scala MANUALE · Gamma/barra"',
        f'gammaPerCandle = input.float({safe_default:.1f}, "Scala MANUALE · Gamma/barra"'
    )
    patch("// EXPORT REVISION: V53-TV-MANUAL-SCALE",
          "// EXPORT REVISION: V53-TV-MANUAL-CLOSE-THREE-COLUMNS")
    patch("// La scala MANUALE e' COMUNE ai profili e non altera i gamma Cboe calcolati.",
          "// I tre profili usano ESATTAMENTE la stessa scala manuale.")
    patch('offsetOdte = input.int(60, "Distanza 0DTE da ultima candela", minval=45, maxval=170)',
          'showPrevious = input.bool(true, "Mostra 0DTE precedente")\n'
          'offsetOdte = input.int(60, "Distanza 0DTE da ultima candela", minval=45, maxval=170, active=not showPrevious)\n'
          'offsetPrevious = input.int(70, "1 · Posizione 0DTE precedente", minval=40, maxval=140, active=showPrevious)\n'
          'offsetOdteCompare = input.int(165, "2 · Posizione 0DTE attuale", minval=120, maxval=300, active=showPrevious)\n'
          'offsetWeeklyCompare = input.int(110, "3 · Distanza Weekly dalle 0DTE attuali", minval=20, maxval=170, active=showPrevious)')
    # Con precedente OFF, due profili semplici alle posizioni iniziali 60 e 160;
    # entrambi rimangono spostabili, con Weekly sempre relativa alle 0DTE.
    patch('offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=300)',
          'offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=300, active=not showPrevious)')
    patch("var array<float> odteLevels = ",
          "var array<float> prevLevels = " +
          _pine_array(previous_odte["rows"], "strike_spx", float(offset)) +
          "\nvar array<float> prevValues = " +
          _pine_array(previous_odte["rows"], "net_gamma") +
          "\nvar array<float> odteLevels = ")
    patch("if barstate.islast and validTicker\n",
          "// Calcola SOLO il minimo SUGGERITO per separare le barre adiacenti.\n"
          "// Non cambia mai la scala manuale inserita dall'utente.\n"
          "requiredGapScale(array<float> leftGamma, array<float> rightGamma, int leftCenter, int rightCenter) =>\n"
          "    float pos = 0.0\n"
          "    float neg = 0.0\n"
          "    for g in leftGamma\n"
          "        pos := math.max(pos, g)\n"
          "    for g in rightGamma\n"
          "        neg := math.max(neg, -g)\n"
          "    float room = float(rightCenter - leftCenter - 12)\n"
          "    room > 0 ? (pos + neg) / room : 1000000.0\n\n"
          "if barstate.islast and validTicker\n")

    anchor = "    float actualScale = gammaPerCandle\n"
    if code.count(anchor) != 1:
        raise RuntimeError("Blocco scala manuale Pine non trovato")
    prefix, _ = code.split(anchor, 1)
    # Sostituiamo il solo blocco di disegno V53 del Pine a due colonne:
    # entrambi i profili correnti e il precedente vengono da snapshot reali.
    code = prefix + f"""    float actualScale = gammaPerCandle
    bool adjusted = false
    int prevCenter = offsetPrevious
    int currentCenter = showPrevious ? offsetOdteCompare : offsetOdte
    int weeklyCenter = currentCenter + (showPrevious ? offsetWeeklyCompare : offsetWeekly)
    bool validOrder = not showPrevious or (prevCenter < currentCenter and currentCenter < weeklyCenter)
    float minimumSafe = math.max(requiredScale(odteValues, currentCenter), requiredScale(weeklyValues, weeklyCenter))
    if showPrevious
        minimumSafe := math.max(minimumSafe, requiredScale(prevValues, prevCenter))
    float recommendedScale = showPrevious ? math.max(requiredGapScale(prevValues, odteValues, prevCenter, currentCenter), requiredGapScale(odteValues, weeklyValues, currentCenter, weeklyCenter)) : requiredGapScale(odteValues, weeklyValues, currentCenter, weeklyCenter)
    if not validOrder
        label wrongOrder = label.new(bar_index + 10, close, "Posizioni: 0DTE precedente < 0DTE attuale < Weekly", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, wrongOrder)
    else if actualScale + 0.000001 < minimumSafe
        label unsafe = label.new(bar_index + 10, close, "Scala manuale insufficiente per limite TradingView +500: aumenta ad almeno " + str.tostring(math.ceil(minimumSafe * 10) / 10.0, "#.0") + " G/bar", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, unsafe)
    else
        string recommendedText = "Scala ottimale " + str.tostring(math.ceil(recommendedScale * 10) / 10.0, "#.0")
        string currentTitle = showPrevious ? "0DTE {current_slot}" : "0DTE {current_slot}"
        string weeklyTitle = "Weekly {current_slot}"
        if showTitles
            label optimal = label.new(bar_index + currentCenter, array.max(odteLevels) + math.max(18.0, (array.max(odteLevels) - array.min(odteLevels)) / 8), recommendedText, xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_down, color=color.new(color.black, 35), textcolor=color.white, size=size.normal)
            array.push(drawnLabels, optimal)
        if showPrevious
            renderProfile(prevLevels, prevValues, bar_index + prevCenter, "0DTE {previous_slot}", actualScale, adjusted)
        renderProfile(odteLevels, odteValues, bar_index + currentCenter, currentTitle, actualScale, adjusted)
        renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, weeklyTitle, actualScale, adjusted)
"""
    return code

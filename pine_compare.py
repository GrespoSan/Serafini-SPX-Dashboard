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
    previous_display = previous_odte.get("display_rows") or previous_odte["rows"]
    current_display = current_odte.get("display_rows") or current_odte["rows"]
    weekly_display = current_weekly.get("display_rows") or current_weekly["rows"]
    if not 50 <= len(previous_display) <= 140:
        raise ValueError("0DTE precedente: richiesti 50-140 strike reali")
    original_by_strike = {float(r["strike_spx"]): float(r["net_gamma"])
                          for r in previous_odte["rows"]}
    extended_by_strike = {float(r["strike_spx"]): float(r["net_gamma"])
                          for r in previous_display}
    if len(extended_by_strike) != len(previous_display) or not all(
            k in extended_by_strike and abs(v - extended_by_strike[k]) < 0.000001
            for k, v in original_by_strike.items()):
        raise ValueError("0DTE precedente: estensione non coerente con i 50 originali")
    base_default = _initial_manual_scale([
        ({"rows": current_display}, 100),
        ({"rows": weekly_display}, 200)
    ])
    # Il primo profilo usa l'offset principale, gli altri lo seguono.
    comparison_default = _initial_manual_scale([
        ({"rows": previous_display}, 100),
        ({"rows": current_display}, 195),
        ({"rows": weekly_display}, 295)
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
    # Il parametro offsetOdte fissa SEMPRE il primo profilo 0DTE visibile:
    # con precedente ON e' quello precedente, con OFF e' quello corrente.
    # Gli altri due profili seguono automaticamente il primo.
    patch('offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)',
          'showPrevious = input.bool(true, "Mostra 0DTE precedente")\n'
          'offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)\n'
          'offsetOdteCompare = input.int(95, "Distanza 0DTE corrente dalle precedenti", minval=20, maxval=300, active=showPrevious)')
    patch("var array<float> odteLevels = ",
          "var array<float> prevLevels = " +
          _pine_array(previous_display, "strike_spx", float(offset)) +
          "\nvar array<float> prevValues = " +
          _pine_array(previous_display, "net_gamma") +
          "\nvar array<float> prevCoreValues = " +
          _pine_array(previous_odte["rows"], "net_gamma") +
          f"\nfloat prevCoreLow = {min(float(r['strike_spx']) for r in previous_odte['rows']) + offset:.8f}" +
          f"\nfloat prevCoreHigh = {max(float(r['strike_spx']) for r in previous_odte['rows']) + offset:.8f}" +
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
    // La distanza dalla candela vale per le 0DTE precedenti (se visibili),
    // altrimenti direttamente per le 0DTE correnti.
    // Spostando offsetOdte si sposta tutto il gruppo: precedente, corrente, Weekly.
    int prevCenter = offsetOdte
    int currentCenter = offsetOdte + (showPrevious ? offsetOdteCompare : 0)
    int weeklyCenter = currentCenter + offsetWeekly
    bool validOrder = not showPrevious or ((not showCurrentOdte or prevCenter < currentCenter) and (not showCurrentWeekly or prevCenter < weeklyCenter) and (not (showCurrentOdte and showCurrentWeekly) or currentCenter < weeklyCenter))
    bool beyondLimit = (showCurrentOdte and currentCenter >= 480) or (showCurrentWeekly and weeklyCenter >= 480) or (showPrevious and prevCenter >= 480)
    array<float> activePrevious = showExtension ? prevValues : prevCoreValues
    array<float> activeCurrent = showExtension ? odteValues : odteCoreValues
    array<float> activeWeekly = showExtension ? weeklyValues : weeklyCoreValues
    float minimumSafe = math.max(showCurrentOdte ? requiredScale(activeCurrent, currentCenter) : 0.0, showCurrentWeekly ? requiredScale(activeWeekly, weeklyCenter) : 0.0)
    if showPrevious
        minimumSafe := math.max(minimumSafe, requiredScale(activePrevious, prevCenter))
    float recommendedScale = 0.0
    if showPrevious and showCurrentOdte
        recommendedScale := math.max(recommendedScale, requiredGapScale(activePrevious, activeCurrent, prevCenter, currentCenter))
    if showCurrentOdte and showCurrentWeekly
        recommendedScale := math.max(recommendedScale, requiredGapScale(activeCurrent, activeWeekly, currentCenter, weeklyCenter))
    if showPrevious and showCurrentWeekly and not showCurrentOdte
        recommendedScale := math.max(recommendedScale, requiredGapScale(activePrevious, activeWeekly, prevCenter, weeklyCenter))
    if not validOrder
        label wrongOrder = label.new(bar_index + 10, close, "Posizioni: 0DTE precedente < 0DTE attuale < Weekly", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, wrongOrder)
    else if beyondLimit
        label warningLimit = label.new(bar_index + 10, close, "Distanze oltre il limite TradingView (+500 barre): riduci la distanza Weekly/0DTE.", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, warningLimit)
    else if actualScale + 0.000001 < minimumSafe
        label unsafe = label.new(bar_index + 10, close, "Scala manuale insufficiente per limite TradingView +500: aumenta ad almeno " + str.tostring(math.ceil(minimumSafe * 10) / 10.0, "#.0") + " G/bar", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, unsafe)
    else
        string recommendedText = "Scala ottimale " + str.tostring(math.ceil(recommendedScale * 10) / 10.0, "#.0")
        string currentTitle = showPrevious ? "0DTE {current_slot}" : "0DTE {current_slot}"
        string weeklyTitle = "Weekly {current_slot}"
        if showTitles and recommendedScale > 0 and (showCurrentOdte or showCurrentWeekly)
            int captionCenter = showCurrentOdte ? currentCenter : weeklyCenter
            float captionTop = showCurrentOdte ? array.max(odteLevels) : array.max(weeklyLevels)
            float captionBottom = showCurrentOdte ? array.min(odteLevels) : array.min(weeklyLevels)
            label optimal = label.new(bar_index + captionCenter, captionTop + math.max(18.0, (captionTop - captionBottom) / 8), recommendedText, xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_down, color=color.new(color.black, 35), textcolor=color.white, size=size.normal)
            array.push(drawnLabels, optimal)
        if showPrevious
            renderProfile(prevLevels, prevValues, bar_index + prevCenter, "0DTE {previous_slot}", actualScale, adjusted, prevCoreLow, prevCoreHigh)
        if showCurrentOdte
            renderProfile(odteLevels, odteValues, bar_index + currentCenter, currentTitle, actualScale, adjusted, odteCoreLow, odteCoreHigh)
        if showCurrentWeekly
            renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, weeklyTitle, actualScale, adjusted, weeklyCoreLow, weeklyCoreHigh)
"""
    return code

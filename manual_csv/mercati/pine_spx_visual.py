"""Shared TradingView Pine v6 VISUAL standard, taken from Serafini SPX export.

Only the presentation layer is shared: manual common Gamma/bar scale,
0DTE offset 60 and relative Weekly offset 100, colors, dotted vertical baselines,
5px Gamma segments, headings, ticker guard and TradingView line-limit check.
Market data, contract selection and price transformations stay separate.

Reference: GrespoSan/Serafini-SPX-Dashboard/pine_export.py (V53-TV-MANUAL-SCALE).
"""
from textwrap import indent


def spx_inputs(scale: str, scale_step: str, primary_label: str = "0DTE",
               tooltip: str = "") -> str:
    """The same visual controls and defaults for NDX, GLD and USO."""
    tooltip = tooltip or (
        "Scala grafica MANUALE e comune a entrambi i profili. "
        "Nessuna modifica ai NET GAMMA originali.")
    tooltip = tooltip.replace('"', "'").replace("\n", " ")
    # La firma conserva primary_label per compatibilita' con i generatori ETF.
    return f'''gammaPerCandle = input.float({scale}, "Scala MANUALE · Gamma/barra", minval=0.000001, step={scale_step}, tooltip="{tooltip}")
offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)
offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)
showCurrentOdte = input.bool(true, "Mostra 0DTE corrente")
showCurrentWeekly = input.bool(true, "Mostra Weekly corrente")
showTop3Odte = input.bool(false, "Proietta Top 3 Call / Put 0DTE")
positiveColor = input.color(color.rgb(152, 255, 152), "NET GAMMA positivo")
negativeColor = input.color(color.rgb(249, 132, 229), "NET GAMMA negativo")
showTitles = input.bool(true, "Mostra intestazioni")'''


# Identical chart geometry to the SPX template: xloc.bar_index, one manual
# Gamma/bar for both groups, green/right and red/left, dotted baseline,
# 5-pixel horizontal segments, heading above profile. No status/disclaimer
# text is drawn over live candles.
SPX_RENDER_FUNCTIONS = """requiredScale(array<float> gamma, int offsetBars) =>
    float maxPos = 0.0
    float maxNeg = 0.0
    for g in gamma
        maxPos := math.max(maxPos, g)
        maxNeg := math.max(maxNeg, -g)
    float rightCapacity = float(math.max(1, 480 - offsetBars))
    float leftCapacity = float(9500 + offsetBars)
    math.max(maxPos / rightCapacity, maxNeg / leftCapacity)

renderProfile(array<float> levels, array<float> gamma, int center, string heading, float actualScale) =>
    int count = array.size(levels)
    if count > 0
        float bottom = array.get(levels, 0)
        float top = array.get(levels, count - 1)
        line baseline = line.new(center, bottom, center, top, xloc=xloc.bar_index, extend=extend.none, color=color.gray, style=line.style_dotted, width=1)
        array.push(drawnLines, baseline)
        for j = 0 to count - 1
            float level = array.get(levels, j)
            float g = array.get(gamma, j)
            int length = g != 0 ? math.max(1, int(math.round(math.abs(g) / actualScale))) : 0
            if length > 0
                int farX = center + (g > 0 ? length : -length)
                line segment = line.new(center, level, farX, level, xloc=xloc.bar_index, extend=extend.none, color=g >= 0 ? positiveColor : negativeColor, width=5)
                array.push(drawnLines, segment)
        if showTitles
            float spacing = count > 1 ? math.max(5.0, (top - bottom) / 20.0) : 10.0
            label t = label.new(center, top + spacing, heading, xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.black, 50), textcolor=color.white, style=label.style_label_down, size=size.normal)
            array.push(drawnLabels, t)

// Solo i 3 NET GAMMA 0DTE positivi/negativi piu' forti della fotografia.
// Linee dal margine dell'ULTIMA candela verso destra; nessuna label.
drawOdteTopProjections(array<float> callLevels, array<float> putLevels) =>
    int nCall = array.size(callLevels)
    if nCall > 0
        for j = 0 to nCall - 1
            float y = array.get(callLevels, j)
            line lc = line.new(bar_index, y, bar_index + 1, y, xloc=xloc.bar_index, extend=extend.right, color=positiveColor, style=line.style_dashed, width=1)
            array.push(drawnLines, lc)
    int nPut = array.size(putLevels)
    if nPut > 0
        for j = 0 to nPut - 1
            float y = array.get(putLevels, j)
            line lp = line.new(bar_index, y, bar_index + 1, y, xloc=xloc.bar_index, extend=extend.right, color=negativeColor, style=line.style_dashed, width=1)
            array.push(drawnLines, lp)"""


def spx_last_bar(main_call: str, weekly_call: str, main_values: str,
                 weekly_values: str, prepare: str = "",
                 top3_levels: tuple[str, str] | None = None) -> str:
    """Draw SPX-style overlays on the last bar, with only error labels.

    'prepare' can map ETF strikes onto future y-coordinates before rendering.
    It never changes Gamma arrays and is run only when the ticker is valid.
    """
    prefix = (indent(prepare.strip(), "    ") + "\n") if prepare.strip() else ""
    main = ("if showCurrentOdte\n            " + main_call) if main_call else "// Profilo principale assente"
    weekly = ("if showCurrentWeekly\n            " + weekly_call) if weekly_call else "// Profilo Weekly assente"
    # Top 3 remain visible even when the histogram is hidden or the
    # user-chosen graphic scale exceeds Pine's histogram limit.
    top3 = (f"\n    if showTop3Odte\n        "
            f"drawOdteTopProjections({top3_levels[0]}, {top3_levels[1]})"
            if top3_levels is not None else "")
    return f'''if barstate.islast and validTicker
    for ln in drawnLines
        line.delete(ln)
    array.clear(drawnLines)
    for lb in drawnLabels
        label.delete(lb)
    array.clear(drawnLabels)
{prefix}    float actualScale = gammaPerCandle
    int weeklyCenter = offsetOdte + offsetWeekly
    bool beyondLimit = (showCurrentOdte and array.size({main_values}) > 0 and offsetOdte >= 480) or (showCurrentWeekly and array.size({weekly_values}) > 0 and weeklyCenter >= 480)
    float minimumSafe = math.max(showCurrentOdte ? requiredScale({main_values}, offsetOdte) : 0.0, showCurrentWeekly ? requiredScale({weekly_values}, weeklyCenter) : 0.0)
    if beyondLimit
        label warningLimit = label.new(bar_index + 10, close, "Distanza Weekly/0DTE oltre il limite TradingView (+500 barre): riduci la distanza.", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, warningLimit)
    else if actualScale < minimumSafe * 0.999999
        label unsafe = label.new(bar_index + 10, close, "Scala manuale troppo piccola. Imposta almeno " + str.tostring(minimumSafe, "#.########") + " G/bar", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.orange, textcolor=color.black, size=size.small)
        array.push(drawnLabels, unsafe)
    else
        {main}
        {weekly}{top3}'''

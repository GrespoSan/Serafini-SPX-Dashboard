"""Graphical-only Edge GEX levels for static Serafini Pine v6 exports."""
from math import isfinite


def append_gex_overlays(code, levels, offset):
    """Customize styles without recalculating Wall/Flip inside TradingView."""
    if not isfinite(float(offset)):
        raise ValueError("Differenziale ES-SPX non valido")
    if "showGexLevels = input.bool" in code:
        raise ValueError("Blocco GEX già presente")

    def price(key):
        raw = (levels or {}).get(key)
        if raw is None or isinstance(raw, bool):
            return "na"
        try:
            value = float(raw) + float(offset)
        except (ValueError, TypeError, OverflowError):
            return "na"
        return f"{value:.8f}" if isfinite(value) and value > 0 else "na"

    call = price("call_wall_spx")
    put = price("put_wall_spx")
    flip = price("gamma_flip_spx")

    return code + f"""
// GEX EDGE 0DTE: i livelli provengono SOLO dalla fotografia di questo Pine.
// Call/Put Wall Edge NON sono i Top 3 NET GAMMA del metodo Serafini.
// Questo indicatore NON scarica opzioni live e non ricalcola Wall/Flip.
// Un livello non verificato resta na, mai ricavato da un altro rilevamento.
gexGroup = "GEX Edge 0DTE — livelli"
showGexLevels = input.bool(true, "Mostra Call Wall / Put Wall / Gamma Flip", group=gexGroup)
gexCallColor = input.color(color.rgb(120, 220, 35), "Call Wall", group=gexGroup, inline="cw")
gexCallWidth = input.int(2, "Spessore", minval=1, maxval=5, group=gexGroup, inline="cw")
gexPutColor = input.color(color.rgb(150, 40, 230), "Put Wall", group=gexGroup, inline="pw")
gexPutWidth = input.int(2, "Spessore", minval=1, maxval=5, group=gexGroup, inline="pw")
gexFlipColor = input.color(color.rgb(175, 175, 175), "Gamma Flip puntinato", group=gexGroup, inline="gf")
gexFlipWidth = input.int(1, "Spessore", minval=1, maxval=5, group=gexGroup, inline="gf")
float gexCallES = {call}
float gexPutES = {put}
float gexFlipES = {flip}
// Una sola quota quando CW e PW coincidono entro mezzo tick ES/MES.
// Le due linee rimangono al prezzo REALE: nessuno spostamento artificiale.
bool gexWallsCoincident = not na(gexCallES) and not na(gexPutES) and math.abs(gexCallES - gexPutES) <= syminfo.mintick * 0.5
var line gexCallLine = na
var line gexPutLine = na
var line gexFlipLine = na
var label gexCallLabel = na
var label gexPutLabel = na
var label gexFlipLabel = na
if barstate.islast
    if not na(gexCallLine)
        line.delete(gexCallLine)
        gexCallLine := na
    if not na(gexPutLine)
        line.delete(gexPutLine)
        gexPutLine := na
    if not na(gexFlipLine)
        line.delete(gexFlipLine)
        gexFlipLine := na
    if not na(gexCallLabel)
        label.delete(gexCallLabel)
        gexCallLabel := na
    if not na(gexPutLabel)
        label.delete(gexPutLabel)
        gexPutLabel := na
    if not na(gexFlipLabel)
        label.delete(gexFlipLabel)
        gexFlipLabel := na
    if validTicker and showGexLevels
        if not na(gexCallES)
            gexCallLine := line.new(bar_index, gexCallES, bar_index + 1, gexCallES, xloc=xloc.bar_index, extend=extend.both, color=gexCallColor, style=line.style_solid, width=gexCallWidth)
            gexCallLabel := label.new(chart.left_visible_bar_time, gexCallES, (gexWallsCoincident ? "GEX CW = PW " : "GEX CW ") + str.tostring(gexCallES, format.mintick), xloc=xloc.bar_time, yloc=yloc.price, style=label.style_label_left, color=color.new(color.black, 75), textcolor=(gexWallsCoincident ? color.white : gexCallColor), size=size.small)
        if not na(gexPutES)
            // Confluenza reale: base verde solida e puntini viola sopra,
            // una sola etichetta. Quando diversi, PW torna solida viola.
            gexPutLine := line.new(bar_index, gexPutES, bar_index + 1, gexPutES, xloc=xloc.bar_index, extend=extend.both, color=gexPutColor, style=(gexWallsCoincident ? line.style_dotted : line.style_solid), width=gexPutWidth)
            if not gexWallsCoincident
                gexPutLabel := label.new(chart.left_visible_bar_time, gexPutES, "GEX PW " + str.tostring(gexPutES, format.mintick), xloc=xloc.bar_time, yloc=yloc.price, style=label.style_label_left, color=color.new(color.black, 75), textcolor=gexPutColor, size=size.small)
        if not na(gexFlipES)
            gexFlipLine := line.new(bar_index, gexFlipES, bar_index + 1, gexFlipES, xloc=xloc.bar_index, extend=extend.both, color=gexFlipColor, style=line.style_dotted, width=gexFlipWidth)
            gexFlipLabel := label.new(chart.left_visible_bar_time, gexFlipES, "GEX GF " + str.tostring(gexFlipES, format.mintick), xloc=xloc.bar_time, yloc=yloc.price, style=label.style_label_left, color=color.new(color.black, 75), textcolor=gexFlipColor, size=size.small)
"""

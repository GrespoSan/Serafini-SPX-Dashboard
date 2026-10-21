"""Pine Script v6 exporter of already computed Serafini net-gamma profiles.
Graphical-only translation to TradingView, no recomputation of exposure.
"""
from math import isfinite
import re


def _pine_array(rows, field: str, offset: float = 0.0):
    vals = []
    for row in rows:
        number = float(row[field]) + (offset if field == 'strike_spx' else 0.0)
        if not isfinite(number):
            raise ValueError('Non-finite profile value')
        
        formatted = f'{number:.8f}'.rstrip('0').rstrip('.')
        vals.append(formatted if '.' in formatted else formatted + '.0')
    if not vals:
        return 'array.new_float()'
    # Keep each line manageable in Pine Editor, even for 50 strikes.
    return 'array.from(\n        ' + ',\n        '.join(', '.join(vals[i:i + 8]) for i in range(0, len(vals), 8)) + '\n    )'


def make_pine(odte: dict, weekly: dict, offset: float, date_text: str, cboe_stamp: str, acquisition_label: str = '') -> str:
    if not isfinite(offset):
        raise ValueError('Il differenziale non è finito')
    if not odte.get('rows') or not weekly.get('rows'):
        raise ValueError('I due profili devono avere dati')
    for group in (odte, weekly):
        if len(group['rows']) > 50:
            raise ValueError('Al massimo 50 livelli per profilo')
    same_exp = odte['expiry'] == weekly['expiry']
    origin = re.sub(r'[^0-9T: .-]', '', cboe_stamp)[:72]
    # Usa solo etichette del registro di acquisizione, non presume il fuso del timestamp Cboe.
    clean_label = re.sub(r'[^a-zA-Z0-9À-ÿ :()._-]', '', acquisition_label or '')[:50]
    tag = f' | {clean_label}' if clean_label else ''
    warning = ('// NOTA: 0DTE e Weekly hanno LA STESSA SCADENZA in questa data: i due profili sono identici per definizione.\n' if same_exp else '')
    return f'''//@version=6
indicator("Serafini SPX NET GAMMA | {date_text}{tag}", overlay=true, max_lines_count=250, max_labels_count=20)
// EXPORT REVISION: V47-FIXED-ABSOLUTE-GAMMA-SCALE
// Rilevamento selezionato: {clean_label or "Non identificato"}
// Esportazione STATICA da uno snapshot Cboe; aggiornare ricopiando il Pine generato dalla dashboard.
// Fonte: {origin}
// Data snapshot: {date_text} | 0DTE: {odte['expiry']} | Weekly: {weekly['expiry']}
// Differenziale ES-SPX GIA' APPLICATO agli strike: {offset:.4f}
// Formula originale: (OI_CALL+VOL_CALL)*GAMMA_CALL - (OI_PUT+VOL_PUT)*GAMMA_PUT.
// 25 strike SOPRA e 25 SOTTO la chiusura SPX: regola confermata direttamente da Serafini.
// Profili graficamente separati come slide 139: 0DTE vicino alle candele, Weekly piu' a destra.
{warning}// Scala NET GAMMA fissa: le barre sono confrontabili tra snapshot con lo stesso gammaPerCandle.
// Non calcola gamma flip e non usa dati opzioni live in TradingView.

gammaPerCandle = input.float(5.0, "Scala fissa: punti NET GAMMA per candela", minval=0.1, step=0.5, tooltip="Mantieni lo STESSO valore per tutti gli snapshot: con scala 5, +50 Gamma sono 10 candele e +150 Gamma sono 30 candele.")
offsetOdte = input.int(48, "Distanza 0DTE da ultima candela", minval=45, maxval=170)
offsetWeekly = input.int(100, "Distanza Weekly da ultima candela", minval=55, maxval=300)
positiveColor = input.color(color.lime, "NET GAMMA positivo")
negativeColor = input.color(color.red, "NET GAMMA negativo")
showTitles = input.bool(true, "Mostra intestazioni")

var array<line> drawnLines = array.new_line()
var array<label> drawnLabels = array.new_label()

// I valori sotto sono i NET GAMMA calcolati sullo snapshot selezionato.
// Il differenziale sposta solo i prezzi, non modifica i valori NET GAMMA.
var array<float> odteLevels = {_pine_array(odte['rows'], 'strike_spx', offset)}
var array<float> odteValues = {_pine_array(odte['rows'], 'net_gamma')}
var array<float> weeklyLevels = {_pine_array(weekly['rows'], 'strike_spx', offset)}
var array<float> weeklyValues = {_pine_array(weekly['rows'], 'net_gamma')}

renderProfile(array<float> levels, array<float> gamma, int center, string heading) =>
    int count = array.size(levels)
    if count > 0
        float bottom = array.get(levels, 0)
        float top = array.get(levels, count - 1)
        line baseline = line.new(center, bottom, center, top, xloc=xloc.bar_index, extend=extend.none, color=color.gray, style=line.style_dotted, width=1)
        array.push(drawnLines, baseline)
        for j = 0 to count - 1
            float level = array.get(levels, j)
            float g = array.get(gamma, j)
            int length = g != 0 ? math.max(1, int(math.round(math.abs(g) / gammaPerCandle))) : 0
            if length > 0
                int farX = center + (g > 0 ? length : -length)
                line segment = line.new(center, level, farX, level, xloc=xloc.bar_index, extend=extend.none, color=g >= 0 ? positiveColor : negativeColor, width=5)
                array.push(drawnLines, segment)
        if showTitles
            float spacing = count > 1 ? math.max(5.0, (top - bottom) / 20.0) : 10.0
            label t = label.new(center, top + spacing, heading, xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.black, 50), textcolor=color.white, style=label.style_label_down, size=size.small)
            array.push(drawnLabels, t)

if barstate.islast
    for segment in drawnLines
        line.delete(segment)
    array.clear(drawnLines)
    for t in drawnLabels
        label.delete(t)
    array.clear(drawnLabels)
    renderProfile(odteLevels, odteValues, bar_index + offsetOdte, "0DTE {odte['expiry']}{tag}")
    renderProfile(weeklyLevels, weeklyValues, bar_index + offsetWeekly, "WEEKLY {weekly['expiry']}{tag}")
'''

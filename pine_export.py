"""Pine Script v6 exporter of already computed Serafini net-gamma profiles.
Graphical-only translation to TradingView, no recomputation of exposure.
"""
from math import isfinite, ceil
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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


def _top_gamma_rows(rows, positive: bool, limit: int = 3):
    """Only existing CBOE strikes: strongest net-positive / net-negative 0DTE bars.

    The same source rows used for the histogram are ranked by net magnitude.
    For equal magnitude, choose the lower strike consistently. No levels are
    synthesized and zero-net rows cannot be counted as either side.
    """
    selected = []
    for row in rows:
        gamma = float(row['net_gamma'])
        strike = float(row['strike_spx'])
        if not isfinite(gamma) or not isfinite(strike):
            raise ValueError('Non-finite 0DTE top-level value')
        if (gamma > 0 and positive) or (gamma < 0 and not positive):
            selected.append(row)
    return sorted(selected, key=lambda r: (-abs(float(r['net_gamma'])),
                                            float(r['strike_spx'])))[:limit]


def _initial_manual_scale(profiles):
    """Scala iniziale sicura per xloc.bar_index +500; nessuna riscalatura dei Gamma."""
    minimum = 1.0
    for profile, center in profiles:
        positives = [max(0.0, float(row["net_gamma"])) for row in profile["rows"]]
        negatives = [max(0.0, -float(row["net_gamma"])) for row in profile["rows"]]
        if not positives:
            continue
        needed = max(max(positives) / float(480 - center),
                     max(negatives) / float(9500 + center))
        minimum = max(minimum, needed)
    return ceil(minimum * 10.0 - 1e-8) / 10.0


def make_pine(odte: dict, weekly: dict, offset: float, date_text: str, cboe_stamp: str, acquisition_label: str = '', *, current_only: bool = False) -> str:
    if not isfinite(offset):
        raise ValueError('Il differenziale non è finito')
    if not odte.get('rows') or not weekly.get('rows'):
        raise ValueError('I due profili devono avere dati')
    for group in (odte, weekly):
        if len(group['rows']) != 50:
            raise ValueError('Il profilo Serafini originale deve restare 25+25')
        visual = group.get('display_rows') or group['rows']
        if not 50 <= len(visual) <= 140:
            raise ValueError('Il Pine visivo ammette 50-140 strike per profilo')
        core = {float(x['strike_spx']): float(x['net_gamma'])
                for x in group['rows']}
        extended = {float(x['strike_spx']): float(x['net_gamma'])
                    for x in visual}
        if len(extended) != len(visual) or not all(
                k in extended and abs(extended[k] - v) < 0.000001
                for k, v in core.items()):
            raise ValueError('Gli strike estesi devono contenere i 50 originali invariati')
    odte_display = odte.get('display_rows') or odte['rows']
    weekly_display = weekly.get('display_rows') or weekly['rows']
    # Dotted/dashed horizontal projections rank ONLY the same 0DTE bars that
    # the chosen histogram mode displays. Weekly never supplies a top level.
    top_call_full = _top_gamma_rows(odte_display, positive=True)
    top_put_full = _top_gamma_rows(odte_display, positive=False)
    top_call_core = _top_gamma_rows(odte['rows'], positive=True)
    top_put_core = _top_gamma_rows(odte['rows'], positive=False)
    default_scale = _initial_manual_scale([
        ({"rows": odte_display}, 100), ({"rows": weekly_display}, 200)
    ])
    same_exp = odte['expiry'] == weekly['expiry']
    origin = re.sub(r'[^0-9T: .-]', '', cboe_stamp)[:72]
    # Usa solo etichette del registro di acquisizione, non presume il fuso del timestamp Cboe.
    clean_label = re.sub(r'[^a-zA-Z0-9À-ÿ :()._-]', '', acquisition_label or '')[:50]
    tag = f' | {clean_label}' if clean_label else ''
    # Solo Emergency: i registri CBOE delle 08:28 e 14:49 UTC del
    # 02/11/2026 coincidono con i download GitHub 08:28 e 14:50 UTC.
    # Il payload non dichiara il fuso: la base UTC e' una deduzione empirica,
    # riportata come tale nel Pine. Convertire con Europe/Rome (DST reale).
    if current_only:
        try:
            source_utc = datetime.strptime(
                str(cboe_stamp).strip(), '%Y-%m-%d %H:%M:%S'
            ).replace(tzinfo=timezone.utc)
            source_rome = source_utc.astimezone(ZoneInfo('Europe/Rome'))
        except (ValueError, TypeError) as exc:
            raise ValueError('Ora CBOE non valida: etichetta Pine non generabile') from exc
        local_clock = source_rome.strftime('%H:%M')
        odte_heading = f'0DTE {local_clock} Roma'
        weekly_heading = f'Weekly {local_clock} Roma'
        indicator_title = (
            'Serafini SPX NET GAMMA | ' +
            source_rome.strftime('%Y-%m-%d %H:%M') + ' Roma'
        )
        clock_note = (
            '// Ora Roma (Europe/Rome, DST automatico): ' + local_clock +
            '; conversione da timestamp CBOE assumendo UTC, ' +
            'come riscontrato dai download GitHub (fuso non dichiarato nel JSON).\n'
            '// Timestamp originale CBOE: ' + str(cboe_stamp).strip() +
            "; non confondere con l'ora effettiva del download.\n"
        )
    else:
        # Intestazioni associate allo slot realmente esportato, mai fisse a 07:30.
        display_clock = '20:30' if '20:30' in clean_label else '07:30'
        odte_heading = f'0DTE {display_clock}'
        weekly_heading = f'Weekly {display_clock}'
        indicator_title = f'Serafini SPX NET GAMMA | {date_text}{tag}'
        clock_note = ''
    warning = ('// NOTA: 0DTE e Weekly hanno LA STESSA SCADENZA in questa data: i due profili sono identici per definizione.\n' if same_exp else '')
    return f'''//@version=6
indicator("{indicator_title}", overlay=true, max_lines_count=500, max_labels_count=20)
// EXPORT REVISION: V53-TV-MANUAL-SCALE
// Rilevamento selezionato: {clean_label or "Non identificato"}
// Esportazione STATICA da uno snapshot Cboe; aggiornare ricopiando il Pine generato dalla dashboard.
// Fonte: {origin}
{clock_note}// Data snapshot: {date_text} | 0DTE: {odte['expiry']} | Weekly: {weekly['expiry']}
// Differenziale ES-SPX GIA' APPLICATO agli strike: {offset:.4f}
// Formula originale: (OI_CALL+VOL_CALL)*GAMMA_CALL - (OI_PUT+VOL_PUT)*GAMMA_PUT.
// Metodo Serafini INVARIATO: 25 strike SOPRA e 25 SOTTO per C1/P1 e analisi.\n// Estensione grafica: strike Cboe veri della stessa fotografia, MAX 140 per profilo; non modifica i 50 originali.
// Profili graficamente separati come slide 139: 0DTE vicino alle candele, Weekly piu' a destra.
{warning}// Scala MANUALE, unica per tutti i profili e mai adattata dal codice.
// Se la scala selezionata supera il limite grafico di TradingView (+500 barre),
// viene mostrato un avviso: aumentare manualmente il valore e riprovare.
// Nessuna modifica, troncamento o normalizzazione dei NET GAMMA.
// Non calcola gamma flip e non usa dati opzioni live in TradingView.

gammaPerCandle = input.float({default_scale:.1f}, "Scala MANUALE · Gamma/barra", minval=0.1, step=0.1, tooltip="Sempre applicata esattamente come impostata. Se troppo bassa, il grafico segnala di aumentarla: nessuna modifica automatica.")
offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)
offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)\nshowExtension = input.bool(true, "Estensione automatica NET GAMMA")
showCurrentOdte = input.bool(true, "Mostra 0DTE corrente")
showCurrentWeekly = input.bool(true, "Mostra Weekly corrente")
showTop3Odte = input.bool(true, "Proietta Top 3 Call / Put 0DTE")
positiveColor = input.color(color.rgb(0, 143, 76), "NET GAMMA positivo")
negativeColor = input.color(color.rgb(242, 54, 69), "NET GAMMA negativo")
showTitles = input.bool(true, "Mostra intestazioni")
labelSizeChoice = input.string("Normale", "Dimensione scritte Pine", options=["Piccola", "Normale", "Grande", "Molto grande"], tooltip="Regola la dimensione di intestazioni, avvisi e news. Default Normale.")
labelTextSize = switch labelSizeChoice
    "Piccola" => size.small
    "Normale" => size.normal
    "Grande" => size.large
    => size.huge
// Nessun livello SPX su sottostanti estranei: solo futures ES/MES.
validTicker = syminfo.type == "futures" and (syminfo.root == "ES" or syminfo.root == "MES")

var array<line> drawnLines = array.new_line()
var array<label> drawnLabels = array.new_label()

// I valori sotto sono i NET GAMMA calcolati sullo snapshot selezionato.
// Il differenziale sposta solo i prezzi, non modifica i valori NET GAMMA.
var array<float> odteLevels = {_pine_array(odte_display, 'strike_spx', offset)}
var array<float> odteValues = {_pine_array(odte_display, 'net_gamma')}
var array<float> odteCoreValues = {_pine_array(odte['rows'], 'net_gamma')}
// Top 3 selezionati dalle barre effettive 0DTE, con segni e prezzi originali.
var array<float> topCallFull = {_pine_array(top_call_full, 'strike_spx', offset)}
var array<float> topPutFull = {_pine_array(top_put_full, 'strike_spx', offset)}
var array<float> topCallCore = {_pine_array(top_call_core, 'strike_spx', offset)}
var array<float> topPutCore = {_pine_array(top_put_core, 'strike_spx', offset)}
var array<float> weeklyLevels = {_pine_array(weekly_display, 'strike_spx', offset)}
var array<float> weeklyValues = {_pine_array(weekly_display, 'net_gamma')}
var array<float> weeklyCoreValues = {_pine_array(weekly['rows'], 'net_gamma')}
float odteCoreLow = {min(float(r['strike_spx']) for r in odte['rows']) + offset:.8f}
float odteCoreHigh = {max(float(r['strike_spx']) for r in odte['rows']) + offset:.8f}
float weeklyCoreLow = {min(float(r['strike_spx']) for r in weekly['rows']) + offset:.8f}
float weeklyCoreHigh = {max(float(r['strike_spx']) for r in weekly['rows']) + offset:.8f}

// Resta un margine di 20 barre rispetto al limite xloc.bar_index +500.
// Evita anche di spingersi oltre le 10.000 barre storiche a sinistra.
// La scala MANUALE e' COMUNE ai profili e non altera i gamma Cboe calcolati.
requiredScale(array<float> gamma, int offsetBars) =>
    float maxPos = 0.0
    float maxNeg = 0.0
    for g in gamma
        maxPos := math.max(maxPos, g)
        maxNeg := math.max(maxNeg, -g)
    float rightCapacity = float(math.max(1, 480 - offsetBars))
    float leftCapacity = float(9500 + offsetBars)
    math.max(maxPos / rightCapacity, maxNeg / leftCapacity)

renderProfile(array<float> levels, array<float> gamma, int center, string heading, float actualScale, bool adjusted, float coreLow, float coreHigh) =>
    int count = array.size(levels)
    if count > 0
        float bottom = showExtension ? array.get(levels, 0) : coreLow
        float top = showExtension ? array.get(levels, count - 1) : coreHigh
        line baseline = line.new(center, bottom, center, top, xloc=xloc.bar_index, extend=extend.none, color=color.gray, style=line.style_dotted, width=1)
        array.push(drawnLines, baseline)
        for j = 0 to count - 1
            float level = array.get(levels, j)
            float g = array.get(gamma, j)
            int length = g != 0 ? math.max(1, int(math.round(math.abs(g) / actualScale))) : 0
            if length > 0 and (showExtension or (level >= coreLow and level <= coreHigh))
                int farX = center + (g > 0 ? length : -length)
                line segment = line.new(center, level, farX, level, xloc=xloc.bar_index, extend=extend.none, color=g >= 0 ? positiveColor : negativeColor, width=5)
                array.push(drawnLines, segment)
        if showTitles
            float spacing = count > 1 ? math.max(5.0, (top - bottom) / 20.0) : 10.0
            string scaleText = " | scala comune " + str.tostring(actualScale, "#.######") + " Gamma/bar" + (adjusted ? " (adattata)" : "")
            label t = label.new(center, top + spacing, heading, xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.black, 50), textcolor=color.white, style=label.style_label_down, size=labelTextSize)
            array.push(drawnLabels, t)

// Come nell'esempio: la proiezione parte dall'ULTIMA candela e prosegue
// solo a destra. I livelli non sono linee a tutto grafico e non hanno label.
drawOdteTopProjections(array<float> callLevels, array<float> putLevels) =>
    int countCall = array.size(callLevels)
    if countCall > 0
        for j = 0 to countCall - 1
            float y = array.get(callLevels, j)
            line c = line.new(bar_index, y, bar_index + 1, y, xloc=xloc.bar_index, extend=extend.right, color=positiveColor, style=line.style_dashed, width=1)
            array.push(drawnLines, c)
    int countPut = array.size(putLevels)
    if countPut > 0
        for j = 0 to countPut - 1
            float y = array.get(putLevels, j)
            line p = line.new(bar_index, y, bar_index + 1, y, xloc=xloc.bar_index, extend=extend.right, color=negativeColor, style=line.style_dashed, width=1)
            array.push(drawnLines, p)

if barstate.islast and validTicker
    for segment in drawnLines
        line.delete(segment)
    array.clear(drawnLines)
    for t in drawnLabels
        label.delete(t)
    array.clear(drawnLabels)
    float actualScale = gammaPerCandle
    bool adjusted = false
    int weeklyCenter = offsetOdte + offsetWeekly
    bool beyondLimit = (showCurrentOdte and offsetOdte >= 480) or (showCurrentWeekly and weeklyCenter >= 480)
    float minimumSafe = math.max(showCurrentOdte ? requiredScale(showExtension ? odteValues : odteCoreValues, offsetOdte) : 0.0, showCurrentWeekly ? requiredScale(showExtension ? weeklyValues : weeklyCoreValues, weeklyCenter) : 0.0)
    if beyondLimit
        label warningLimit = label.new(bar_index + 10, close, "Distanza Weekly/0DTE oltre il limite TradingView (+500 barre): riduci la distanza. Nessun valore modificato automaticamente.", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.new(color.orange, 15), textcolor=color.black, size=labelTextSize)
        array.push(drawnLabels, warningLimit)
    else if actualScale + 0.000001 < minimumSafe
        label warningLabel = label.new(bar_index + 10, close, "Scala manuale troppo piccola per TradingView. Imposta almeno " + str.tostring(math.ceil(minimumSafe * 10) / 10.0, "#.0") + " G/bar", xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, color=color.new(color.orange, 15), textcolor=color.black, size=labelTextSize)
        array.push(drawnLabels, warningLabel)
    else
        if showCurrentOdte
            renderProfile(odteLevels, odteValues, bar_index + offsetOdte, "{odte_heading}", actualScale, adjusted, odteCoreLow, odteCoreHigh)
        if showCurrentWeekly
            renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, "{weekly_heading}", actualScale, adjusted, weeklyCoreLow, weeklyCoreHigh)
    // Proiezioni indipendenti dalla scala del profilo e dalle Weekly.
    if showTop3Odte
        drawOdteTopProjections(showExtension ? topCallFull : topCallCore, showExtension ? topPutFull : topPutCore)
'''

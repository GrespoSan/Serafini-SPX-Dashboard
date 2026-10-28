"""Aggiunge alle esportazioni Pine le news già selezionate dal calendario della dashboard.
Non modifica in alcun modo la costruzione dei profili NET GAMMA.
"""
import re


def append_news(script, events):
    # Più eventi nello stesso minuto condividono una linea e una sola etichetta.
    unique = {}
    for event in events:
        if not re.fullmatch(r"\d{2}:\d{2}", str(event.get("clock", ""))):
            continue
        hh, mm = map(int, event["clock"].split(":"))
        if hh > 23 or mm > 59:
            continue
        day = event["day"]
        key = (day.year, day.month, day.day, hh, mm)
        unique.setdefault(key, []).append(event)
    if not unique:
        return script
    lines = [
        "",
        "// News del calendario macro della dashboard: orari Europe/Rome.",
        "// Le linee vengono create anche PRIMA dell'evento sul grafico.",
        'showMacroNews = input.bool(true, "News macro · linee verticali")',
        'var array<line> macroNewsLines = array.new_line()',
        'var array<label> macroNewsLabels = array.new_label()',
        'float macroTop = ta.highest(high, 300) + ta.atr(14) * 7.0',
        'if barstate.islast',
        '    for ln in macroNewsLines',
        '        line.delete(ln)',
        '    array.clear(macroNewsLines)',
        '    for lb in macroNewsLabels',
        '        label.delete(lb)',
        '    array.clear(macroNewsLabels)',
        '    if showMacroNews and timeframe.isintraday',
    ]
    for index, (key, rows) in enumerate(sorted(unique.items())[:12]):
        y, mo, d, hh, mm = key
        names = " / ".join(sorted(set(str(row["kind"]) for row in rows)))
        names = names.replace("\\\\", "/").replace('"', "'")[:95]
        major = any(row.get("priority") == "ALTA" for row in rows)
        news_color = "color.red" if major else "color.orange"
        short = "FOMC" if "FOMC" in names else ("FED" if "Conferenza" in names else names[:20])
        label_text = f"{hh:02d}:{mm:02d} " + r"\n".join(short.upper())
        lines.extend([
            f'        int newsTime{index} = timestamp("Europe/Rome", {y}, {mo}, {d}, {hh}, {mm})',
            f'        line newsLine{index} = line.new(newsTime{index}, low, newsTime{index}, high, xloc=xloc.bar_time, extend=extend.both, color={news_color}, style=line.style_dashed, width=1)',
            f'        array.push(macroNewsLines, newsLine{index})',
            f'        label newsLabel{index} = label.new(newsTime{index}, macroTop, "{label_text}", xloc=xloc.bar_time, yloc=yloc.price, style=label.style_none, color=color.new({news_color}, 100), textcolor={news_color}, size=size.large, textalign=text.align_left)',
            f'        array.push(macroNewsLabels, newsLabel{index})',
        ])
    return script + "\n" + "\n".join(lines) + "\n"

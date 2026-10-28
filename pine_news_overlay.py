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
        "// News dal calendario macro USA della dashboard, orario Europe/Rome.",
        "// Linee informative: indipendenti da gamma, profili e livelli.",
        'showMacroNews = input.bool(true, "News macro · linee verticali")',
    ]
    for index, (key, rows) in enumerate(sorted(unique.items())[:12]):
        y, mo, d, hh, mm = key
        names = " / ".join(sorted(set(str(row["kind"]) for row in rows)))
        names = names.replace("\\", "/").replace('"', "'")[:95]
        major = any(row.get("priority") == "ALTA" for row in rows)
        color = "color.red" if major else "color.orange"
        lines.extend([
            f'newsTime{index} = timestamp("Europe/Rome", {y}, {mo}, {d}, {hh}, {mm})',
            f'if showMacroNews and timeframe.isintraday and barstate.isconfirmed and time <= newsTime{index} and time_close > newsTime{index}',
            f'    line.new(newsTime{index}, low, newsTime{index}, high, xloc=xloc.bar_time, extend=extend.both, color={color}, style=line.style_dashed, width=1)',
            f'    label.new(newsTime{index}, high, "{hh:02d}:{mm:02d} Roma · {names}", xloc=xloc.bar_time, yloc=yloc.abovebar, style=label.style_label_down, color={color}, textcolor=color.white, size=size.tiny)',
        ])
    return script + "\n" + "\n".join(lines) + "\n"

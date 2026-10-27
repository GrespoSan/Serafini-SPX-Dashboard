"""Esportazione GIF on-demand del replay storico (nessuna modifica ai dati).
I fotogrammi sono SOLO gli snapshot realmente disponibili, non interpolazioni.
"""
from datetime import datetime, time, timedelta
from io import BytesIO
import math
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from replay_prices import candles_up_to, load_es_candles
from replay_scale import dynamic_gamma_bounds

ROME = ZoneInfo("Europe/Rome")
SIZE = (1440, 810)
BG, GRID, WHITE, MUTED = "#0e1117", "#273341", "#e7edf5", "#9ba8b8"
GREEN, RED, GOLD = "#22c55e", "#ef4444", "#fbbf24"


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def generate_gif(available, day, kind, offset):
    """Crea una GIF leggibile usando gli stessi frame Cboe e OHLC del replay.
    Le barre 5m devono essere chiuse al momento del download dello snapshot.
    Restituisce (gif_bytes, numero_frame).
    """
    if len(available) < 2:
        raise ValueError("Per creare la GIF servono almeno due rilevamenti reali.")
    if offset is None or not math.isfinite(float(offset)):
        raise ValueError("Inserisci un differenziale ES - SPX valido prima di esportare.")

    offset = float(offset)
    prices = load_es_candles(day)
    download_times = [
        datetime.combine(day, time.fromisoformat(item["clock"]), tzinfo=ROME)
        for _, item in available
    ]
    prices_last = candles_up_to(prices, max(download_times))
    if prices_last.empty:
        raise ValueError("Nessuna candela ES chiusa disponibile fino all'ultimo snapshot.")

    rows = [row for _, item in available
            for row in item["profiles"][kind]["rows"]]
    strikes = sorted({float(row["strike_spx"]) for row in rows})
    gammas = [float(row["net_gamma"]) for row in rows]
    if not strikes or not all(math.isfinite(x) for x in gammas):
        raise ValueError("Profilo NET GAMMA incompleto.")
    levels = [strike + offset for strike in strikes]
    low = min(min(levels), float(prices_last["low"].min())) - 8.0
    high = max(max(levels), float(prices_last["high"].max())) + 8.0
    if high <= low:
        raise ValueError("Intervallo prezzi non valido.")
    # Scala orizzontale ricalcolata per ogni singolo fotogramma.
    # Non normalizza i NET GAMMA e non cambia i valori sottostanti.

    chart_top, chart_bottom = 100, 695
    candle_left, candle_right = 90, 845
    gamma_left, gamma_right = 965, 1370
    tick_start = datetime.combine(day, time(0, 0), tzinfo=ROME)
    tick_end = max(max(download_times) + timedelta(minutes=5),
                   datetime.combine(day, time(18, 35), tzinfo=ROME))
    total_seconds = max((tick_end - tick_start).total_seconds(), 1.)

    def yprice(value):
        return chart_bottom - (float(value) - low) / (high - low) * (chart_bottom - chart_top)

    def xtime(value):
        return candle_left + (value - tick_start).total_seconds() / total_seconds * (candle_right - candle_left)


    font_title = _font(22, True)
    font_sub = _font(14)
    font_caption = _font(12)
    font_small = _font(10)

    images = []
    for index, ((slot, item), downloaded_at) in enumerate(zip(available, download_times)):
        im = Image.new("RGB", SIZE, BG)
        draw = ImageDraw.Draw(im)
        draw.text((30, 19), f"NET GAMMA {kind}  |  {day:%d/%m/%Y}  |  rilevamento {slot} Roma",
                  font=font_title, fill=WHITE)
        draw.text((30, 57),
                  f"Download {item['clock']} Roma  |  {item.get('origin', 'fonte non indicata')}  |  "
                  f"ES-SPX {offset:+g}  |  {index+1}/{len(available)} rilevamenti",
                  font=font_sub, fill=MUTED)

        # Entrambi i pannelli condividono la scala verticale dei prezzi ES.
        draw.text((candle_left, 79), "ES=F - candele 5 minuti (chiuse allo snapshot)",
                  font=font_caption, fill=WHITE)
        current = {float(row["strike_spx"]): float(row["net_gamma"])
                   for row in item["profiles"][kind]["rows"]}
        gmin, gmax = dynamic_gamma_bounds(list(current.values()))
        def xgamma(value):
            return gamma_left + (float(value) - gmin) / (gmax - gmin) * (gamma_right - gamma_left)
        gamma_zero = xgamma(0)
        draw.text((gamma_left, 79),
                  f"NET GAMMA {kind} | scala {gmin:+.0f} / {gmax:+.0f}",
                  font=font_caption, fill=WHITE)
        for n in range(7):
            value = low + n * (high - low) / 6.0
            y = round(yprice(value))
            draw.line((candle_left, y, candle_right, y), fill=GRID, width=1)
            draw.text((25, y - 6), f"{value:,.0f}", fill=MUTED, font=font_caption)
        draw.line((candle_left, chart_bottom, candle_right, chart_bottom), fill=GRID, width=2)
        for hour in (0, 3, 6, 9, 12, 15, 18, 21):
            stamp = tick_start + timedelta(hours=hour)
            if stamp > tick_end:
                continue
            px = round(xtime(stamp))
            draw.text((px - 13, chart_bottom + 8), f"{hour:02d}:00",
                      font=font_small, fill=MUTED)

        # Nessuna candela successiva all'ora effettiva di download.
        visible = candles_up_to(prices_last, downloaded_at)
        bar_width = max(2, min(5, round((5 * 60 / total_seconds) * (candle_right - candle_left) * .72)))
        for candle in visible.itertuples(index=False):
            values = (float(candle.open), float(candle.high),
                      float(candle.low), float(candle.close))
            if not all(math.isfinite(v) for v in values):
                continue
            px = round(xtime(candle.bar_close))
            op, hi, lo, cl = values
            color = GREEN if cl >= op else RED
            draw.line((px, round(yprice(hi)), px, round(yprice(lo))), fill=color, width=1)
            top = round(min(yprice(op), yprice(cl)))
            bottom = round(max(yprice(op), yprice(cl)))
            draw.rectangle((px - bar_width // 2, top, px + bar_width // 2,
                            max(top + 1, bottom)), fill=color)

        # Zero e limiti gamma adattivi al frame: non sono grandezze
        # confrontabili a colpo d'occhio fra orari differenti.
        draw.line((round(gamma_zero), chart_top, round(gamma_zero), chart_bottom),
                  fill="#607080", width=2)
        draw.text((gamma_left, chart_bottom + 8), f"{gmin:+.0f}",
                  font=font_small, fill=MUTED)
        draw.text((round(gamma_zero) - 5, chart_bottom + 8), "0",
                  font=font_small, fill=MUTED)
        draw.text((gamma_right - 30, chart_bottom + 8), f"{gmax:+.0f}",
                  font=font_small, fill=MUTED)
        for strike in strikes:
            if strike not in current:
                continue
            net = current[strike]
            if net == 0.:
                continue
            py = round(yprice(strike + offset))
            px = round(xgamma(net))
            z = round(gamma_zero)
            color = GREEN if net > 0 else RED
            draw.rectangle((min(px, z), py - 3, max(px, z) + 1, py + 3), fill=color)
            # Stessa coordinata ES della barra. Strike SPX solo nei dati sorgente.
            label = f"{strike + offset:,.0f}"
            if net > 0:
                box = draw.textbbox((0, 0), label, font=font_small)
                draw.text((z - 7 - (box[2] - box[0]), py - 6),
                          label, font=font_small, fill=WHITE)
            else:
                draw.text((z + 7, py - 6), label, font=font_small, fill=WHITE)

        # Animazione delle acquisizioni, NON del prezzo tick by tick.
        x1, x2 = 90, 1370
        draw.line((x1, 763, x2, 763), fill=GRID, width=7)
        proportion = index / max(len(available) - 1, 1)
        draw.line((x1, 763, x1 + (x2-x1) * proportion, 763),
                  fill=GOLD, width=7)
        draw.text((90, 731), "Snapshot disponibili, senza interpolazione dei profili gamma",
                  font=font_caption, fill=MUTED)
        draw.text((950, 731), "Fonte prezzi: Yahoo ES=F 5m", font=font_caption, fill=MUTED)
        draw.text((30, 787), "Studio storico 0DTE. Scala Gamma dinamica per frame: confronta i valori, non le lunghezze delle barre.",
                  font=font_small, fill=MUTED)
        images.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=80))

    durations = [700] * (len(images) - 1) + [1500]
    buffer = BytesIO()
    images[0].save(buffer, format="GIF", save_all=True,
                   append_images=images[1:], duration=durations,
                   loop=0, optimize=True, disposal=2)
    return buffer.getvalue(), len(images)

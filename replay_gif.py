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
from replay_profiles import choose_rows
from replay_view import plotted_rows, price_zoom_bounds

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


def generate_gif(available, day, kind, offset, show_extended=True,
                 band_pct=3.0, auto_extend=True, zoom_complete=False):
    """Crea una GIF leggibile usando gli stessi frame Cboe e OHLC del replay.
    Le barre 5m devono essere chiuse al momento del download dello snapshot.
    Restituisce (gif_bytes, numero_frame).
    """
    if len(available) < 2:
        raise ValueError("Per creare la GIF servono almeno due rilevamenti reali.")
    if offset is None or not math.isfinite(float(offset)):
        raise ValueError("Inserisci un differenziale ES - SPX valido prima di esportare.")

    offset = float(offset)
    download_times = []
    for _, item in available:
        stamp = item.get("download_rome")
        if not stamp:
            raise ValueError("GIF: timestamp download Replay non disponibile")
        recorded = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            raise ValueError("GIF: timestamp download Replay senza fuso")
        download_times.append(recorded.astimezone(ROME))
    prices = load_es_candles(day, required_until=max(download_times))
    prices_last = candles_up_to(prices, max(download_times))
    if prices_last.empty:
        raise ValueError("Nessuna candela ES chiusa disponibile fino all'ultimo snapshot.")

    def display_rows(item):
        return plotted_rows(
            item["profiles"][kind], item["reference_spx"], item.get("spot"),
            band_pct, auto_extend, show_extended,
        )

    rows = [row for _, item in available for row in display_rows(item)]
    strikes = sorted({float(row["strike_spx"]) for row in rows})
    gammas = [float(row["net_gamma"]) for row in rows]
    if not strikes or not all(math.isfinite(x) for x in gammas):
        raise ValueError("Profilo NET GAMMA incompleto.")
    levels = [strike + offset for strike in strikes]
    all_spots = []
    for _, frame in available:
        try:
            v = float(frame.get("spot"))
            if math.isfinite(v) and v > 0:
                all_spots.append(v + offset)
        except (ValueError, TypeError, OverflowError):
            continue
    low, high = price_zoom_bounds(
        all_spots, prices_last["low"].tolist(), prices_last["high"].tolist(),
        levels, float(available[0][1]["reference_spx"]) + offset,
        complete=zoom_complete,
    )
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
                  f"ES-SPX {offset:+g}  |  fascia ±{band_pct:g}%  |  "
                  f"{index+1}/{len(available)} rilevamenti",
                  font=font_sub, fill=MUTED)

        # Entrambi i pannelli condividono la scala verticale dei prezzi ES.
        draw.text((candle_left, 79), "ES futures - candele 5 minuti (chiuse allo snapshot)",
                  font=font_caption, fill=WHITE)
        current = {float(row["strike_spx"]): float(row["net_gamma"])
                   for row in display_rows(item)}
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
            # Clip esplicito al riquadro: lo zoom automatico non deve
            # disegnare fuori dal grafico in presenza di prezzi fuori scala.
            if hi < low or lo > high:
                continue
            color = GREEN if cl >= op else RED
            wick_top = round(max(chart_top, min(chart_bottom, yprice(hi))))
            wick_bottom = round(max(chart_top, min(chart_bottom, yprice(lo))))
            draw.line((px, wick_top, px, wick_bottom), fill=color, width=1)
            top = round(max(chart_top, min(chart_bottom,
                            min(yprice(op), yprice(cl)))))
            bottom = round(max(chart_top, min(chart_bottom,
                               max(yprice(op), yprice(cl)))))
            draw.rectangle((px - bar_width // 2, top, px + bar_width // 2,
                            max(top + 1, bottom)), fill=color)

        # Con la fascia estesa le barre rimangono tutte reali; etichettiamo
        # solo le 32 più intense DENTRO lo zoom per evitare sovrapposizioni.
        # Nella modalità Serafini 50 restano le etichette tradizionali.
        visible_labels = [
            (strike, gamma) for strike, gamma in current.items()
            if gamma != 0 and low <= strike + offset <= high
        ]
        label_strikes = {
            strike for strike, _ in (
                sorted(visible_labels, key=lambda x: -abs(x[1]))[:32]
                if show_extended else visible_labels)
        }

        # Limiti simmetrici adattivi: lo zero resta fisso al centro del pannello.
        # Le lunghezze delle barre NON sono confrontabili fra orari differenti.
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
            if py < chart_top or py > chart_bottom:
                continue
            px = round(xgamma(net))
            z = round(gamma_zero)
            color = GREEN if net > 0 else RED
            draw.rectangle((min(px, z), py - 3, max(px, z) + 1, py + 3), fill=color)
            if strike not in label_strikes:
                continue
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
        source_label = prices.attrs.get("candle_source", "Prezzi ES · fonte N/D")
        draw.text((950, 731), f"Fonte: {source_label[:45]}", font=font_caption, fill=MUTED)
        draw.text((30, 787), "Studio storico 0DTE. Zero al centro in ogni frame; barre a scala dinamica, ampiezze non confrontabili.",
                  font=font_small, fill=MUTED)
        images.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=80))

    durations = [700] * (len(images) - 1) + [1500]
    buffer = BytesIO()
    images[0].save(buffer, format="GIF", save_all=True,
                   append_images=images[1:], duration=durations,
                   loop=0, optimize=True, disposal=2)
    return buffer.getvalue(), len(images)

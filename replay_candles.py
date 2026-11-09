"""Visualizzazione opzionale candele ES + istogramma NET GAMMA originale.

Due pannelli Plotly condividono lo stesso asse prezzo; il profilo viene dalla
funzione frame_data() esistente e NON viene ricalcolato o sostituito.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from replay_prices import candles_up_to, load_es_candles

ROME = ZoneInfo("Europe/Rome")


def _frame_download_rome(day, item):
    """Usa il timestamp di acquisizione reale, non uno slot ricostruito."""
    value = item.get("download_rome")
    if not value:
        raise ValueError("Replay: download_rome assente; orario non verificabile")
    try:
        recorded = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Replay: timestamp download non valido") from exc
    if recorded.tzinfo is None:
        raise ValueError("Replay: timestamp download privo di fuso")
    local = recorded.astimezone(ROME)
    if local.date() != day:
        raise ValueError("Replay: data download diversa dalla giornata scelta")
    return local


def _plot_rome_clock(stamp):
    """Solo grafica: ora civile di Roma senza offset per asse Plotly stabile.

    Il timestamp aware resta intatto per scegliere le candele già chiuse.
    """
    if stamp.tzinfo is None:
        raise ValueError("Timestamp candela senza fuso: impossibile localizzare")
    return stamp.astimezone(ROME).replace(tzinfo=None).isoformat(timespec="seconds")


def _time_window_rome(day, last_download):
    """Finestra invariata in ogni frame: inizio giornata fino a fine replay."""
    start = datetime.combine(day, time.min)
    end = max(datetime.combine(day, time(18, 35)),
              last_download.astimezone(ROME).replace(tzinfo=None) + timedelta(minutes=5))
    return [start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")]


def combined_graph(gamma_graph, available, day, kind, offset, gamma_bounds, gamma_ybounds):
    """Crea un grafico animato 5m ES=F (sinistra) + NET GAMMA (destra)."""
    end_of_last_slot = max(_frame_download_rome(day, item) for _, item in available)
    data = load_es_candles(day, required_until=end_of_last_slot)
    chart_time_window = _time_window_rome(day, end_of_last_slot)
    relevant = candles_up_to(data, end_of_last_slot)
    if relevant.empty:
        raise ValueError("Nessuna candela ES=F chiusa prima degli snapshot disponibili")

    # Stesso intervallo prezzi per entrambe le colonne e tutti i frame.
    lows = relevant["low"].astype(float).tolist()
    highs = relevant["high"].astype(float).tolist()
    min_y = min(gamma_ybounds[0], min(lows) - 5)
    max_y = max(gamma_ybounds[1], max(highs) + 5)

    fig = make_subplots(
        rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.035,
        column_widths=[0.62, 0.38],
    )

    def candle_trace(item):
        cutoff = _frame_download_rome(day, item)
        subset = candles_up_to(data, cutoff)
        # Sempre visualizzazione Europe/Rome, mai mixing tra UTC, browser e Roma.
        # La selezione usa invece i timestamp originali timezone-aware.
        clock_axis = [_plot_rome_clock(stamp) for stamp in subset["bar_close"]]
        return go.Candlestick(
            x=clock_axis,
            open=subset["open"].tolist(), high=subset["high"].tolist(),
            low=subset["low"].tolist(), close=subset["close"].tolist(),
            increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
            increasing_fillcolor="#22c55e", decreasing_fillcolor="#ef4444",
            name=data.attrs.get("candle_source", "ES futures · 5m"), showlegend=False,
        )

    first_item = available[0][1]
    fig.add_trace(candle_trace(first_item), row=1, col=1)
    # Riutilizza esattamente le barre NET GAMMA e il prezzo Cboe del Replay corrente.
    fig.add_trace(gamma_graph.data[0], row=1, col=2)
    fig.add_trace(gamma_graph.data[1], row=1, col=2)

    slot_to_item = dict(available)
    fig.frames = [
        go.Frame(
            name=frame.name,
            data=[candle_trace(slot_to_item[frame.name]),
                  frame.data[0], frame.data[1]],
            traces=[0, 1, 2],
            # Fissa anche xaxis (candele) ad ogni step: Plotly non deve
            # ripristinare un autorange incoerente dopo slider/play.
            # xaxis2 resta la scala NET GAMMA dinamica della fotografia.
            layout=go.Layout(
                title=frame.layout.title,
                xaxis=dict(range=chart_time_window, autorange=False),
                xaxis2=dict(range=list(frame.layout.xaxis.range), autorange=False),
            ),
        )
        for frame in gamma_graph.frames
    ]

    # Comandi animazione originali: stessi slot, play, pausa e slider.
    fig.update_layout(
        title=gamma_graph.layout.title,
        template="plotly_dark",
        height=gamma_graph.layout.height,
        margin=dict(l=16, r=16, t=94, b=90),
        updatemenus=gamma_graph.layout.updatemenus,
        sliders=gamma_graph.layout.sliders,
        showlegend=False,
    )
    # Tick e range sono orari CIVILI di Roma senza timezone: Plotly/browser
    # non possono traslare la data né ampliare l'asse al giorno precedente.
    fig.update_xaxes(
        title_text=f"ES futures · 5 minuti · ora Roma {day:%d/%m/%Y}",
        type="date", range=chart_time_window, autorange=False,
        tick0=chart_time_window[0], dtick=3 * 60 * 60 * 1000,
        tickformat="%H:%M", showgrid=True,
        rangeslider_visible=False, row=1, col=1,
    )
    fig.update_xaxes(
        title_text="NET GAMMA", range=gamma_bounds, zeroline=True,
        fixedrange=True, row=1, col=2,
    )
    fig.update_yaxes(
        title_text="Prezzo ES" if offset is not None else "ES / strike SPX (non allineati)",
        range=[min_y, max_y], tickformat=",.0f",
        fixedrange=True, row=1, col=1,
    )
    fig.update_yaxes(
        range=[min_y, max_y], tickformat=",.0f",
        fixedrange=True, row=1, col=2,
    )
    # Fornisci visibilità della provenienza senza alterare tracciati/gamma.
    fig.update_layout(meta={
        "candle_source": data.attrs.get("candle_source", "ES futures · 5m"),
    })
    return fig

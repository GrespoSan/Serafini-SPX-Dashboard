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


def combined_graph(gamma_graph, available, day, kind, offset, gamma_bounds, gamma_ybounds):
    """Crea un grafico animato 5m ES=F (sinistra) + NET GAMMA (destra)."""
    data = load_es_candles(day)
    end_of_last_slot = max(
        datetime.combine(day, time.fromisoformat(item["clock"]), tzinfo=ROME)
        for _, item in available
    )
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
        cutoff = datetime.combine(day, time.fromisoformat(item["clock"]), tzinfo=ROME)
        subset = candles_up_to(data, cutoff)
        return go.Candlestick(
            x=subset["bar_close"].tolist(),
            open=subset["open"].tolist(), high=subset["high"].tolist(),
            low=subset["low"].tolist(), close=subset["close"].tolist(),
            increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
            increasing_fillcolor="#22c55e", decreasing_fillcolor="#ef4444",
            name="ES=F · 5m Yahoo", showlegend=False,
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
            layout=frame.layout,
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
    fig.update_xaxes(
        title_text="ES=F · 5 minuti (ora Roma)",
        range=[
            datetime.combine(day, time(0, 0), tzinfo=ROME),
            datetime.combine(day, time(18, 35), tzinfo=ROME),
        ],
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
    return fig

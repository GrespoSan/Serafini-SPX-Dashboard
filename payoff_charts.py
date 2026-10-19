"""Grafici payoff didattici per la dashboard SPX.

Solo payoff intrinseco a scadenza dei vertical e dello short strangle.
Premi e commissioni sconosciuti -> NON si disegna P&L reale.
I ruoli delle gambe sono ipotesi del corso, non inferibili dai dati NET GAMMA.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st


def intrinsic_payoff(strikes: dict[str, float], kind: str, x: np.ndarray) -> np.ndarray:
    """Valori intrinseci in punti SPX, senza flussi di premi."""
    s = {key: float(value) for key, value in strikes.items()}
    if kind == 'call':
        return np.maximum(x - s['C1'], 0) - np.maximum(x - s['C2'], 0)
    if kind == 'put':
        return np.maximum(s['P1'] - x, 0) - np.maximum(s['P2'] - x, 0)
    if kind == 'strangle':
        return -np.maximum(s['P3'] - x, 0) - np.maximum(x - s['C3'], 0)
    raise ValueError(f'Tipo di strategia non riconosciuto: {kind}')


def make_payoff_figure(strikes: dict[str, float], kind: str, offset: float = 0.0) -> go.Figure:
    s = {key: float(value) for key, value in strikes.items()}
    legs = {'call': ('C1', 'C2'), 'put': ('P2', 'P1'), 'strangle': ('P3', 'C3')}[kind]
    a, b = s[legs[0]], s[legs[1]]
    if a >= b:
        raise ValueError('Gli strike della struttura devono essere ordinati correttamente')
    width = b - a
    margin = max(15.0, width * (0.60 if kind != 'strangle' else 0.28))
    x_spx = np.linspace(a - margin, b + margin, 320)
    # Campioniamo sempre i due strike: spigoli esatti anche con ampiezze piccole.
    x_spx = np.sort(np.unique(np.r_[x_spx, a, b]))
    x = x_spx + offset  # prezzi ES in ascissa, SPX per il payoff
    y = intrinsic_payoff(s, kind, x_spx)
    col = {'call': '#38c97a', 'put': '#ff5269', 'strangle': '#ffa435'}[kind]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode='lines', name='Valore intrinseco netto',
                             line={'color': col, 'width': 3},
                             hovertemplate='ES %{x:.2f}<br>Intrinseco %{y:.2f} pt<extra></extra>'))
    for leg in legs:
        value = s[leg] + offset
        fig.add_vline(x=value, line_color='#a1a1aa', line_width=1, line_dash='dash')
        fig.add_annotation(x=value, y=1.03, yref='paper', text=f'<b>{leg}</b><br>{value:g}',
                           showarrow=False, font={'size': 11, 'color': '#e2e8f0'},
                           bgcolor='rgba(17,24,39,0.8)', borderpad=3)
    fig.add_hline(y=0, line_color='#64748b', line_width=1, line_dash='dot')
    yrange = max(float(np.max(np.abs(y))), width, 1.0)
    fig.update_layout(
        template='plotly_dark', height=310, margin={'l': 12, 'r': 10, 't': 40, 'b': 20},
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font={'size': 11}, showlegend=False,
        xaxis={'title': 'Prezzo ES (SPX + differenziale)', 'range': [float(x[0]), float(x[-1])],
               'tickformat': '.0f', 'gridcolor': '#273142', 'zeroline': False},
        yaxis={'title': 'Intrinseco (punti)', 'range': [-yrange * 1.15, yrange * 1.15],
               'gridcolor': '#273142', 'zeroline': False},
    )
    return fig


def render_payoff_charts(chosen: dict, offset: float | None = None) -> None:
    """Da chiamare SOLO dopo aver validato i sei livelli C1..P3."""
    names = ('C1', 'C2', 'C3', 'P1', 'P2', 'P3')
    if not all(name in chosen for name in names):
        st.info('Seleziona i sei livelli C1–C3 e P1–P3 per visualizzare i payoff.')
        return
    s = {name: float(chosen[name]['strike_spx']) for name in names}
    if not (s['C1'] < s['C2'] < s['C3'] and s['P1'] > s['P2'] > s['P3']):
        st.warning('Correggi l’ordine degli strike per visualizzare i payoff.')
        return
    st.markdown('**Grafici payoff — 2 Vertical + Strangle (sperimentale)**')
    if offset is None:
        st.info('Per mostrare i grafici con i prezzi ES inserisci prima il differenziale ES−SPX.')
        return
    offset = float(offset)
    st.caption('Payoff intrinseco A SCADENZA, espresso in punti SPX, senza premi né commissioni. '
               'Non rappresenta il profitto/perdita effettivo e non dimostra chi abbia comprato o venduto le opzioni.')
    specs = [
        ('call', 'Vertical Call · C1–C2', f"C1 ES {s['C1']+offset:g} · C2 ES {s['C2']+offset:g}"),
        ('put', 'Vertical Put · P1–P2', f"P1 ES {s['P1']+offset:g} · P2 ES {s['P2']+offset:g}"),
        ('strangle', 'Short Strangle · P3/C3', f"P3 ES {s['P3']+offset:g} · C3 ES {s['C3']+offset:g}"),
    ]
    for col, (kind, title, descr) in zip(st.columns(3), specs):
        with col:
            st.markdown(f'**{title}**')
            st.caption(descr)
            st.plotly_chart(make_payoff_figure(s, kind, offset), use_container_width=True,
                            key=f'payoff_{kind}_{offset:g}_{"_".join(str(s[n]) for n in names)}')
    st.caption('Nota: nei grafici dei vertical il valore intrinseco non può diventare negativo '
               'senza considerare il premio pagato. Nello short strangle, fra P3 e C3 '
               'l’intrinseco è zero; il credito iniziale non è noto e non viene inventato.')
    st.caption(f'Asse orizzontale in ES (differenziale {offset:+.2f}). Intrinseco calcolato sugli strike SPX; nessun premio ipotizzato.')

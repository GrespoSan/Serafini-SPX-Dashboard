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
    if kind == 'combined':
        # Esattamente le 6 gambe dei tre grafici precedenti; nessun premio.
        return (intrinsic_payoff(s, 'call', x)
                + intrinsic_payoff(s, 'put', x)
                + intrinsic_payoff(s, 'strangle', x))
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


def make_combined_payoff_figure(strikes: dict[str, float], offset: float = 0.0) -> go.Figure:
    """Somma delle sei gambe, rappresentata sullo stesso prezzo SPX a scadenza.

    I prezzi ES in ascissa sono una traslazione visiva; l'intrinseco rimane
    quello delle opzioni SPX. Non sono inclusi premi, commissioni o moltiplicatori.
    """
    s = {key: float(value) for key, value in strikes.items()}
    ordered = sorted(('P3', 'P2', 'P1', 'C1', 'C2', 'C3'), key=lambda key: s[key])
    values = [s[key] for key in ordered]
    if not (s['P3'] < s['P2'] < s['P1'] and s['C1'] < s['C2'] < s['C3']):
        raise ValueError('Ordine interno dei vertical e dello strangle non valido.')
    span = max(values) - min(values)
    margin = max(20.0, span * 0.18)
    points = np.linspace(values[0] - margin, values[-1] + margin, 600)
    # Vertici agli strike esatti, per mostrare le pendenze corrette.
    x_spx = np.sort(np.unique(np.r_[points, values]))
    x_es = x_spx + offset
    y = intrinsic_payoff(s, 'combined', x_spx)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_es, y=y, mode='lines', name='Intrinseco combinato',
        line={'color': '#60a5fa', 'width': 3},
        hovertemplate='ES %{x:.2f}<br>Intrinseco combinato %{y:+.2f} pt<extra></extra>',
    ))
    for leg in ordered:
        level = s[leg] + offset
        fig.add_vline(x=level, line_color='#94a3b8', line_width=1, line_dash='dash',
                      opacity=0.55)
        fig.add_annotation(x=level, y=1.05, yref='paper',
                           text=f'<b>{leg}</b><br>{level:g}',
                           showarrow=False,
                           font={'size': 10, 'color': '#e2e8f0'},
                           bgcolor='rgba(17,24,39,0.85)', borderpad=3)
    fig.add_hline(y=0, line_color='#94a3b8', line_width=1, line_dash='dot')
    max_abs = max(float(np.max(np.abs(y))), 1.0)
    fig.update_layout(
        template='plotly_dark', height=385,
        margin={'l': 12, 'r': 12, 't': 63, 'b': 30},
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False, font={'size': 12},
        xaxis={'title': 'Prezzo ES stimato (strike SPX + differenziale)',
               'range': [float(x_es[0]), float(x_es[-1])],
               'tickformat': '.0f', 'gridcolor': '#273142', 'zeroline': False},
        yaxis={'title': 'Intrinseco combinato (punti SPX)',
               'range': [-max_abs * 1.2, max_abs * 1.2],
               'gridcolor': '#273142', 'zeroline': False},
    )
    return fig


def render_payoff_charts(chosen: dict, offset: float | None = None,
                         profile_0dte: dict | None = None) -> None:
    """Confronto unico nella sezione Struttura opzionale.

    Pannelli indipendenti ma con identica scala verticale ES:
    a sinistra gamma 0DTE, a destra intrinseco combinato ruotato.
    Non modifica la selezione dei livelli ne' la formula NET GAMMA.
    """
    names = ('C1', 'C2', 'C3', 'P1', 'P2', 'P3')
    if not all(name in chosen for name in names):
        st.info('Seleziona i sei livelli C1–C3 e P1–P3 per il confronto.')
        return
    s = {name: float(chosen[name]['strike_spx']) for name in names}
    if not (s['C1'] < s['C2'] < s['C3'] and s['P1'] > s['P2'] > s['P3']):
        st.warning('Correggi l’ordine dei sei strike per visualizzare la struttura.')
        return
    if offset is None:
        st.info('Inserisci il differenziale ES−SPX per confrontare payoff e Gamma sulla stessa scala ES.')
        return
    rows = (profile_0dte or {}).get('rows') or []
    if not rows:
        st.info('Profilo 0DTE non disponibile: il confronto non può essere costruito.')
        return

    offset = float(offset)
    levels = {key: value + offset for key, value in s.items()}
    gamma_prices = [float(r['strike_spx']) + offset for r in rows]
    gamma_values = [float(r['net_gamma']) for r in rows]
    ylow = min(gamma_prices + list(levels.values())) - 8
    yhigh = max(gamma_prices + list(levels.values())) + 8
    y_range = [ylow, yhigh]

    # Campionare tutti i vertici: la curva ruotata mantiene la geometria
    # esatta delle sei opzioni, senza approssimare gli strike.
    points_spx = np.sort(np.unique(np.r_[
        np.linspace(ylow - offset, yhigh - offset, 600),
        list(s.values()), ylow - offset, yhigh - offset
    ]))
    combined = intrinsic_payoff(s, 'combined', points_spx)

    fig_gamma = go.Figure()
    fig_gamma.add_trace(go.Bar(
        x=gamma_values, y=gamma_prices, orientation='h',
        width=3.8, marker_color=[
            '#22c55e' if value >= 0 else '#ef4444' for value in gamma_values
        ],
        customdata=[float(r['strike_spx']) for r in rows],
        hovertemplate='ES %{y:.2f}<br>SPX %{customdata:.2f}'
                      '<br>NET Gamma %{x:+.3f}<extra></extra>',
        name='NET GAMMA 0DTE',
    ))
    fig_gamma.add_vline(x=0, line_color='#94a3b8', line_width=1)
    fig_gamma.update_layout(
        title='NET GAMMA 0DTE', template='plotly_dark',
        height=670, margin=dict(l=10, r=14, t=54, b=48),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False, bargap=0.1,
        xaxis=dict(title='NET GAMMA', zeroline=True, gridcolor='#273142'),
        yaxis=dict(title='Prezzo ES stimato', range=y_range,
                   tickformat=',.0f', gridcolor='#273142', fixedrange=True),
    )

    fig_payoff = go.Figure()
    fig_payoff.add_trace(go.Scatter(
        x=combined, y=points_spx + offset,
        mode='lines', name='Intrinseco combinato',
        line=dict(color='#60a5fa', width=3),
        hovertemplate='Intrinseco %{x:+.2f} punti SPX'
                      '<br>ES %{y:.2f}<extra></extra>',
    ))
    fig_payoff.add_vline(x=0, line_color='#94a3b8', line_width=1,
                         line_dash='dot')
    for key in ('P3', 'P2', 'P1', 'C1', 'C2', 'C3'):
        price = levels[key]
        marker_color = '#f59e0b' if key.startswith('C') else '#a78bfa'
        for fig in (fig_gamma, fig_payoff):
            fig.add_hline(y=price, line_color=marker_color,
                          line_width=1, line_dash='dash', opacity=0.65)
        fig_payoff.add_annotation(
            x=1.01, xref='paper', y=price, yref='y',
            text=f'<b>{key}</b> {price:,.0f}', showarrow=False,
            xanchor='left', font=dict(size=11, color=marker_color),
        )
    width = max(float(np.max(combined) - np.min(combined)), 1)
    fig_payoff.update_layout(
        title='Payoff combinato — 6 gambe', template='plotly_dark',
        height=670, margin=dict(l=12, r=94, t=54, b=48),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        xaxis=dict(title='Intrinseco teorico (punti SPX)',
                   range=[min(0, float(np.min(combined))) - width * 0.1,
                          max(0, float(np.max(combined))) + width * 0.1],
                   gridcolor='#273142'),
        yaxis=dict(title='Prezzo ES stimato', range=y_range,
                   tickformat=',.0f', gridcolor='#273142', fixedrange=True),
    )

    st.markdown('**Struttura opzionale — NET GAMMA 0DTE e payoff combinato**')
    st.caption('Stesso asse verticale ES e stessi sei livelli. '
               'I livelli restano ipotesi modificabili; il payoff è il valore '
               'intrinseco teorico a scadenza delle sei gambe, senza premi '
               'né commissioni. Non rappresenta il profitto reale.')
    left, right = st.columns(2, gap='small')
    key_suffix = '_'.join(str(s[key]) for key in names)
    with left:
        st.plotly_chart(fig_gamma, use_container_width=True,
                        key=f'structure_gamma_{offset:g}_{key_suffix}')
    with right:
        st.plotly_chart(fig_payoff, use_container_width=True,
                        key=f'structure_combined_{offset:g}_{key_suffix}')
    st.caption(f'Prezzi ES = strike SPX + differenziale {offset:+.2f}. '
               'Le curve rappresentano solo la struttura didattica ipotizzata '
               'da Serafini; il NET GAMMA non prova le posizioni delle opzioni.')

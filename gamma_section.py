"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import gzip
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from net_gamma import profile, profile_csv, read_snapshot
from volatility_levels import weekly_expiry, gamma_comparison, breakout_reference
from context_levels import suggest_buffer
from gamma_compare import compare_profiles
from gamma_brief import select_key_levels, operational_summary, transition_zones
from pine_export import make_pine
from operational_levels import find_candidates, proposed_levels, candidate_csv
from option_structure import build_structure
from payoff_charts import render_payoff_charts
from es_spx_basis import download_and_calculate
from decimal import Decimal, ROUND_HALF_UP
from html import escape


def _transition_summary(rows, direction):
    """Sintesi basata solo sui segni e sulle intensita' osservate negli snapshot."""
    flips_neg = sorted((r for r in rows if r['NET mattina'] > 0 > r['NET pomeriggio']),
                       key=lambda r: r['Strike SPX'], reverse=True)
    flips_pos = sorted((r for r in rows if r['NET mattina'] < 0 < r['NET pomeriggio']),
                       key=lambda r: r['Strike SPX'], reverse=True)

    def level(r):
        price = r.get('Prezzo ES')
        return f"{price:,.0f}".replace(',', '.') if price is not None else f"SPX {r['Strike SPX']:g}"

    if flips_neg and not flips_pos:
        summary = 'Il Gamma negativo si è esteso verso l\'alto. '
        summary += 'I livelli ' + ' e '.join(level(r) for r in flips_neg) + ' sono passati da positivo a negativo'
        lo = min(r['Strike SPX'] for r in flips_neg)
        hi = max(r['Strike SPX'] for r in flips_neg)
        below = sorted((r for r in rows if r['Strike SPX'] < lo and r['NET mattina'] < 0 and
                        r['NET pomeriggio'] < r['NET mattina']), key=lambda r: lo - r['Strike SPX'])
        above = sorted((r for r in rows if r['Strike SPX'] > hi and r['NET mattina'] > 0 and
                        0 < r['NET pomeriggio'] < r['NET mattina']), key=lambda r: r['Strike SPX'] - hi)
        if below:
            summary += '; a ' + level(below[0]) + ' il Gamma negativo è aumentato'
        if above:
            summary += (', mentre ' if below else '; ') + 'il Gamma positivo a ' + level(above[0]) + ' si è indebolito'
        return summary + '.'
    if flips_pos and not flips_neg:
        summary = 'Il Gamma positivo si è esteso verso il basso. '
        summary += 'I livelli ' + ' e '.join(level(r) for r in flips_pos) + ' sono passati da negativo a positivo'
        lo = min(r['Strike SPX'] for r in flips_pos)
        hi = max(r['Strike SPX'] for r in flips_pos)
        above = sorted((r for r in rows if r['Strike SPX'] > hi and r['NET mattina'] > 0 and
                        r['NET pomeriggio'] > r['NET mattina']), key=lambda r: r['Strike SPX'] - hi)
        below = sorted((r for r in rows if r['Strike SPX'] < lo and r['NET mattina'] < 0 and
                        r['NET mattina'] < r['NET pomeriggio'] < 0), key=lambda r: lo - r['Strike SPX'])
        if above:
            summary += '; a ' + level(above[0]) + ' il Gamma positivo è aumentato'
        if below:
            summary += (', mentre ' if above else '; ') + 'il Gamma negativo a ' + level(below[0]) + ' si è indebolito'
        return summary + '.'
    if flips_pos or flips_neg:
        return ('Cambi di segno in entrambe le direzioni: ' +
                ', '.join(level(r) for r in sorted(flips_neg + flips_pos,
                                                  key=lambda r: r['Strike SPX'], reverse=True)) + '.')
    return 'Nessun cambio di segno nella zona.'


def render(store, week, snaps, logs):
    st.header('NET GAMMA · replica formula Serafini — verifica')
    st.info('NET GAMMA invariato (25+25 strike). 0DTE prioritario; Weekly del venerdì = venerdì successivo. C1–C3/P1–P3: proposte modificabili. Volume Profile annuale e deviazione standard si visualizzano su TradingView; qui si possono confrontare i soli valori UP/DOWN già verificati. Esclusi CFD, stocastico e probabilità macro.')
    if not snaps:
        st.info('Nessuno snapshot disponibile nella settimana selezionata.')
        return
    names = sorted((o['name'] for o in snaps), reverse=True)
    choice = st.selectbox('Snapshot originale Cboe', names)
    path = f'{week}/snapshots/{choice}'
    try:
        snap = read_snapshot(store.download(path))
    except Exception as exc:
        st.error(f'Impossibile leggere lo snapshot: {type(exc).__name__}')
        return
    timestamp = str(snap.get('timestamp', ''))
    st.caption(f'Snapshot Cboe: {timestamp} · file {choice} · nessuna modifica al file originale')
    # Si usa la data dello snapshot per gli esempi e per impedire look-ahead.
    try:
        day = date.fromisoformat(choice.split('SPX_CBOE_')[1][:8][:4] + '-' + choice.split('SPX_CBOE_')[1][4:6] + '-' + choice.split('SPX_CBOE_')[1][6:8])
    except Exception:
        st.error('Data dello snapshot non riconosciuta nel nome del file.')
        return
    previous = snap['data'].get('prev_day_close')
    st.caption(f'Chiusura SPX precedente dichiarata nel payload Cboe: {previous} (da confrontare con chiusura cash ufficiale)')
    @st.cache_data(ttl=3600, show_spinner=False)
    def _auto_basis(target_day):
        result = download_and_calculate(target_day)
        # Non memorizziamo frame grafici nel widget di selezione.
        result.pop('bars', None)
        return result

    st.markdown('**Differenziale ES − SPX (sessione precedente)**')
    st.caption('Metodo Serafini: media delle distanze sulle barre a 15 minuti della sessione cash precedente. '
               'Qui usiamo le chiusure sincronizzate, una scelta tecnica da verificare su TradingView.')
    basis_key = f'basis_override_{day.isoformat()}'
    if 'auto_basis_results' not in st.session_state:
        st.session_state.auto_basis_results = {}
    col1, col2 = st.columns(2)
    with col1:
        ref = st.number_input('Chiusura SPX precedente', min_value=0.01,
                              value=float(previous or snap['data']['current_price']),
                              step=0.25, format='%.4f',
                              help='Riferimento per i 50 strike. Verificare con la chiusura cash ufficiale.')
    with col2:
        if st.button('Calcola automaticamente ES − SPX', key=f'auto_basis_{day}'):
            try:
                with st.spinner('Acquisizione e confronto barre ES/SPX a 15 minuti...'):
                    st.session_state.auto_basis_results[day.isoformat()] = _auto_basis(day)
                # Una nuova richiesta propone il nuovo valore ma lascia all’utente
                # la possibilità di modificarlo prima di usarlo.
                result = st.session_state.auto_basis_results[day.isoformat()]
                st.session_state[basis_key] = str(int(Decimal(str(result['basis'])).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
            except Exception as exc:
                st.session_state.auto_basis_results[day.isoformat()] = None
                st.warning(f'Calcolo automatico non disponibile: {str(exc)[:250]}. Inserisci il valore verificato manualmente.')
        offset_txt = st.text_input('Differenziale ES − SPX applicato (modificabile)',
                                   value='', key=basis_key,
                                   placeholder='Es. 47',
                                   help='Valore automatico proposto, sempre correggibile. Vuoto = grafico SPX.')
    auto_result = st.session_state.auto_basis_results.get(day.isoformat())
    if auto_result:
        st.caption(f"Proposta automatica **{auto_result['basis']:.2f} punti → arrotondata a {int(Decimal(str(auto_result['basis'])).quantize(Decimal('1'), rounding=ROUND_HALF_UP))} punti** · "
                   f"seduta {auto_result['reference_date']} · {auto_result['bar_count']} barre a 15 minuti "
                   f"· min {auto_result['minimum']:.2f} / max {auto_result['maximum']:.2f} "
                   f"· ultimo {auto_result['last']:.2f}. Fonte: {auto_result['source']}.")
        st.info('Confronta la proposta con il grafico TradingView ES1! − SPX a 15 minuti. '
                'Yahoo ES=F potrebbe differire dal contratto CME specifico o da TradingView. '
                'Se necessario correggi il campo: prevale sempre il valore che hai verificato.')
    try:
        offset = None if not offset_txt.strip() else float(offset_txt.replace(',', '.'))
    except ValueError:
        st.error('Differenziale non numerico'); return
    weekly_exp = weekly_expiry(day)
    if day.weekday() >= 5:
        st.error('Snapshot in giorno non operativo: scegliere manualmente una giornata valida.'); return
    st.caption(f'0DTE: {day.isoformat()} · Weekly: {weekly_exp.isoformat()} (lun–gio venerdì corrente; venerdì successivo). In caso di festività controllare la scadenza effettiva.')
    # Sintesi breve: due fotografie, stessi strike SPX, una sola visualizzazione.
    with st.expander('Confronto NET GAMMA · Mattina / Pomeriggio (v25)', expanded=True):
        same_day = sorted(n for n in names if n.startswith(f'SPX_CBOE_{day:%Y%m%d}_'))
        if len(same_day) < 2:
            st.info('Servono due snapshot diversi della stessa giornata.')
        else:
            col_a, col_b = st.columns(2)
            with col_a:
                first_name = st.selectbox('Mattina', same_day[:-1], index=0, key=f'v19_first_{day}')
            later = [n for n in same_day if n > first_name]
            if not later:
                st.info('Non ci sono snapshot successivi per il confronto.')
                st.stop()
            with col_b:
                second_name = st.selectbox('Pomeriggio', later, index=len(later)-1,
                                           key=f'v19_second_{day}_{first_name}')
            selected_profile = st.radio('Profilo', ['0DTE', 'Weekly'], horizontal=True,
                                        key=f'v19_profile_{day}', label_visibility='collapsed')
            try:
                older = read_snapshot(store.download(f'{week}/snapshots/{first_name}'))
                newer = read_snapshot(store.download(f'{week}/snapshots/{second_name}'))
                old_ref = older['data'].get('prev_day_close')
                new_ref = newer['data'].get('prev_day_close')
                if old_ref is not None and new_ref is not None and abs(float(old_ref) - float(new_ref)) > 0.001:
                    st.warning('Chiusure SPX precedenti diverse nei due file; il confronto applica a entrambi il riferimento scelto sopra.')
                expiry = day if selected_profile == '0DTE' else weekly_exp
                old_prof = profile(older, expiry, ref)
                new_prof = profile(newer, expiry, ref)
                if not old_prof['rows'] or not new_prof['rows']:
                    st.info(f'Profilo {selected_profile} non disponibile in uno dei due snapshot.')
                else:
                    comparisons, stats = compare_profiles(old_prof, new_prof, offset)
                    main_levels = select_key_levels(comparisons, count_per_sign=4, minimum_separation=5.0)
                    if not main_levels:
                        st.info('Nessuno strike in comune tra i due snapshot.')
                    else:
                        import plotly.graph_objects as go
                        labels = [f"{r['Prezzo ES']:,.0f}".replace(',', '.') if r['Prezzo ES'] is not None
                                  else f"{r['Strike SPX']:g} SPX" for r in main_levels]
                        fig = go.Figure()
                        fig.add_bar(name='Mattina', y=labels,
                                    x=[r['NET mattina'] for r in main_levels], orientation='h', marker_color='#596EFF')
                        fig.add_bar(name='Pomeriggio', y=labels,
                                    x=[r['NET pomeriggio'] for r in main_levels], orientation='h', marker_color='#F2A93B')
                        fig.update_layout(title=f'NET GAMMA {selected_profile} — {day:%d/%m/%Y}',
                                          barmode='group', template='plotly_dark', height=410,
                                          margin=dict(l=10, r=10, t=42, b=15),
                                          legend=dict(orientation='h', y=1.10, x=0),
                                          xaxis_title='NET GAMMA', yaxis_title=None)
                        fig.add_vline(x=0, line_color='#999', line_width=1)
                        st.plotly_chart(fig, use_container_width=True,
                                        key=f'v19_chart_{selected_profile}_{first_name}_{second_name}')
                        
                        for _line in operational_summary(main_levels):
                            st.markdown(_line)
                        if selected_profile == 'Weekly':
                            # Weekly: enfasi sulle concentrazioni di maggiore intensita',
                            # non sulle transizioni di segno di ampiezza minima.
                            st.markdown('#### Concentrazioni Weekly principali')
                            ranked_weekly = sorted(
                                comparisons,
                                key=lambda r: max(abs(float(r['NET mattina'])),
                                                  abs(float(r['NET pomeriggio']))),
                                reverse=True,
                            )[:4]
                            if ranked_weekly:
                                def _weekly_value(v):
                                    color = '#22c55e' if v > 0 else '#ef4444' if v < 0 else '#d1d5db'
                                    label = f"{v:+.2f}".replace('.', ',').replace('-', '−')
                                    return f'<span style="color:{color};font-weight:650">{label}</span>'

                                def _weekly_change(a, b):
                                    if a > 0 > b:
                                        return 'Da positivo a negativo'
                                    if a < 0 < b:
                                        return 'Da negativo a positivo'
                                    if a == 0 or b == 0:
                                        return 'Passaggio per zero' if a != b else 'Invariato'
                                    if abs(b) > abs(a) + 1e-8:
                                        return ('Positivo' if b > 0 else 'Negativo') + ' aumentato'
                                    if abs(b) < abs(a) - 1e-8:
                                        return ('Positivo' if b > 0 else 'Negativo') + ' diminuito'
                                    return 'Invariato'

                                tr = []
                                for r in ranked_weekly:
                                    morning = float(r['NET mattina'])
                                    afternoon = float(r['NET pomeriggio'])
                                    price = r.get('Prezzo ES')
                                    label = (f"{price:,.0f}".replace(',', '.') if price is not None
                                             else f"SPX {r['Strike SPX']:g}")
                                    tr.append(
                                        '<tr>'
                                        f'<td style="padding:9px 12px;border-bottom:1px solid #40454b;font-weight:600">{escape(label)}</td>'
                                        f'<td style="padding:9px 12px;border-bottom:1px solid #40454b;white-space:nowrap">{_weekly_value(morning)} <span style="color:#a1a1aa;padding:0 9px">:</span> {_weekly_value(afternoon)}</td>'
                                        f'<td style="padding:9px 12px;border-bottom:1px solid #40454b">{escape(_weekly_change(morning, afternoon))}</td>'
                                        '</tr>'
                                    )
                                st.markdown(
                                    '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;text-align:left;font-size:0.95rem">'
                                    '<thead><tr style="background:rgba(125,125,125,0.13)"><th style="padding:10px 12px">Livello ES</th>'
                                    '<th style="padding:10px 12px">Mattina : Pomeriggio</th>'
                                    '<th style="padding:10px 12px">Variazione</th></tr></thead><tbody>'
                                    + ''.join(tr) + '</tbody></table></div>',
                                    unsafe_allow_html=True,
                                )
                                st.caption('Quattro concentrazioni principali per intensità NET GAMMA, non quattro livelli operativi. I piccoli cambi di segno Weekly non generano zone automatiche.')
                        else:
                            # Transizioni CONTIGUE osservate nei due snapshot, non segnali di ingresso.
                            zones = transition_zones(comparisons)
                            if zones:
                                st.markdown('#### Evoluzione NET GAMMA — zone di transizione')
                                st.caption('I valori indicano due rilevazioni distinte: Mattina : Pomeriggio. Non si sommano.')
                                def _price(r):
                                    return (f"{r['Prezzo ES']:,.0f}".replace(',', '.') if r['Prezzo ES'] is not None
                                            else f"SPX {r['Strike SPX']:g}")
                                def _colored(v):
                                    color = '#22c55e' if v > 0 else '#ef4444' if v < 0 else '#d1d5db'
                                    value = f"{v:+.2f}".replace('.', ',').replace('-', '−')
                                    return f'<span style="color:{color};font-weight:650;font-variant-numeric:tabular-nums">{value}</span>'
                                def _description(old, new):
                                    if old > 0 > new:
                                        return 'Da positivo a negativo'
                                    if old < 0 < new:
                                        return 'Da negativo a positivo'
                                    if old == 0 or new == 0:
                                        return 'Valore zero / cambio di segno' if old != new else 'Invariato'
                                    if abs(new) > abs(old) + 1e-8:
                                        return ('Positivo' if new > 0 else 'Negativo') + ' aumentato'
                                    if abs(new) < abs(old) - 1e-8:
                                        return ('Positivo' if new > 0 else 'Negativo') + ' diminuito'
                                    return 'Invariato'
                                for num, zone in enumerate(zones, start=1):
                                    rows = sorted(zone['rows'], key=lambda r: r['Strike SPX'], reverse=True)
                                    if len(zones) > 1:
                                        st.markdown(f"**Zona {num} — {escape(zone['direction'])}**")
                                    body = []
                                    for item in rows:
                                        old, new = float(item['NET mattina']), float(item['NET pomeriggio'])
                                        desc = _description(old, new)
                                        emphasis = 'font-weight:650;' if old * new < 0 else ''
                                        body.append(
                                            '<tr>'
                                            f'<td style="padding:9px 12px;border-bottom:1px solid #40454b;font-weight:600;white-space:nowrap">{escape(_price(item))}</td>'
                                            f'<td style="padding:9px 12px;border-bottom:1px solid #40454b;white-space:nowrap">{_colored(old)} <span style="color:#a1a1aa;padding:0 9px">:</span> {_colored(new)}</td>'
                                            f'<td style="padding:9px 12px;border-bottom:1px solid #40454b;{emphasis}">{escape(desc)}</td>'
                                            '</tr>'
                                        )
                                    table_html = ('<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;text-align:left;font-size:0.95rem">'
                                                  '<thead><tr style="background:rgba(125,125,125,0.13)"><th style="padding:10px 12px">Livello ES</th><th style="padding:10px 12px">Mattina : Pomeriggio</th><th style="padding:10px 12px">Variazione</th></tr></thead>'
                                                  '<tbody>' + ''.join(body) + '</tbody></table></div>')
                                    st.markdown(table_html, unsafe_allow_html=True)
                                    st.markdown(f'**Sintesi operativa:** {_transition_summary(rows, zone["direction"])}')
                            else:
                                st.caption('Nessun cambiamento di segno netto nei livelli comuni ai due snapshot.')

                        st.caption('Grafico: fino a 4 Gamma positivi e 4 negativi secondo il segno mattutino; gli strike sono confrontati allo stesso prezzo SPX. I livelli non sono automaticamente Call/Put né segnali operativi.')
                        if stats['solo_mattina'] or stats['solo_pomeriggio']:
                            st.caption(f"Strike confrontati: {stats['comuni']} · non comuni: "
                                       f"{stats['solo_mattina'] + stats['solo_pomeriggio']}.")
            except Exception as exc:
                st.error(f'Confronto non disponibile: {type(exc).__name__}: {str(exc)[:220]}')

    computed_profiles = {}
    candidate_0dte = None
    if day.weekday() == 4:
        st.info('Venerdì: il profilo 0DTE è quello di oggi, mentre il Weekly usa il venerdì SUCCESSIVO (conferma diretta di Serafini).')
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        try:
            result = profile(snap, expiry, ref)
        except ValueError as exc:
            st.error(f'Impossibile costruire il profilo {title}: {exc}')
            continue
        computed_profiles[title] = result
        if title == '0DTE' and result['rows']:
            candidate_0dte = find_candidates(result['rows'], ref)
        st.subheader(f'{title} · scadenza {expiry.isoformat()}')
        if not result['rows']:
            st.warning('Nessun contratto trovato per questa scadenza nello snapshot: nessun risultato inventato.')
            continue
        st.caption(f"{result['contracts']} contratti · {result['available_strikes']} strike disponibili · {len(result['rows'])} strike selezionati")
        records = [{**r, 'strike_es': r['strike_spx'] + offset if offset is not None else None} for r in result['rows']]
        import pandas as pd
        frame = pd.DataFrame(records)
        # Slide 144: profilo NET GAMMA, non componenti separate.
        # Il lato della barra deriva esclusivamente dal segno del NET GAMMA calcolato.
        # Nessun Gamma Flip imposto o stimato arbitrariamente.
        import plotly.graph_objects as go
        y = frame['strike_es' if offset is not None else 'strike_spx'].tolist()
        net = frame['net_gamma'].astype(float).tolist()
        colors = ['#2dbb72' if v >= 0 else '#e45757' for v in net]
        fig = go.Figure()
        fig.add_bar(
            x=net, y=y, orientation='h', name='NET GAMMA',
            marker_color=colors,
            customdata=frame[['call_gamma_component', 'put_gamma_component']].to_numpy(),
            hovertemplate=(
                'Strike %{y:,.2f}<br>NET GAMMA: %{x:,.3f}'
                '<br>Call: %{customdata[0]:,.3f}'
                '<br>Put: %{customdata[1]:,.3f}<extra></extra>'
            ),
        )
        fig.update_layout(
            height=max(520, len(y) * 15),
            margin=dict(l=15, r=20, t=40, b=30),
            title='NET GAMMA per strike — positivo a destra / negativo a sinistra',
            xaxis=dict(title='NET GAMMA (unità della formula Serafini)', zeroline=True,
                       zerolinewidth=1, zerolinecolor='#b7bec8'),
            yaxis=dict(title='Strike ES' if offset is not None else 'Strike SPX',
                       tickmode='array', tickvals=y,
                       ticktext=[f'{v:g}' for v in y], type='linear'),
            showlegend=False,
            template='plotly_dark',
        )
        fig.add_vline(x=0, line_width=1, line_color='#b7bec8')
        st.plotly_chart(fig, use_container_width=True, key=f'gamma_net_{title}_{choice}')
        st.caption('Gamma positivo (verde) e negativo (rosso) sono il segno del NET GAMMA per strike. Il Gamma Flip non viene calcolato né disegnato in questa versione: la sua definizione operativa resta da validare sul materiale Serafini.')
        with st.expander(f'Dettaglio numerico {title} (50 strike)', expanded=False):
            st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(f'Scarica calcolo CSV {title}', profile_csv(result, offset),
                           file_name=f'NET_GAMMA_{title}_{day.isoformat()}_{choice}.csv', mime='text/csv', key=f'csv_{title}_{choice}')
    if candidate_0dte is not None:
        with st.expander('Livelli operativi C/P — tre barre più intense (SPERIMENTALE v10)', expanded=False):
            st.warning('Questi sono CANDIDATI basati sulle tre barre NET GAMMA più intense per lato, NON livelli C1–C3/P1–P3 confermati da Serafini. Non sono segnali automatici. Il calcolo NET GAMMA resta invariato.')
            st.caption('Criterio fisso di prova: tra i 25 strike sopra la chiusura SPX, le tre barre NET GAMMA positive più lunghe; tra i 25 sotto, le tre barre negative con valore assoluto maggiore. Nessun confronto con le barre vicine. C1→C3 in ordine crescente di prezzo, P1→P3 in ordine decrescente.')
            suggestions = proposed_levels(candidate_0dte)
            chosen = {}
            for side, prefix, direction in [('call', 'C', 'Call'), ('put', 'P', 'Put')]:
                entries = candidate_0dte[side]
                st.markdown(f'**{direction} — {len(entries)} barre eleggibili; prime tre proposte per intensità**')
                if len(entries) < 3:
                    st.info('Meno di tre barre del segno richiesto: non si inventano livelli mancanti.')
                # Tre selettori indipendenti, con i suggerimenti come valori iniziali.
                options = [None] + [e['strike_spx'] for e in entries]
                option_index = {e['strike_spx']: e for e in entries}
                cols = st.columns(3)
                for n in (1, 2, 3):
                    label = f'{prefix}{n}'
                    default = suggestions.get(label, {}).get('strike_spx')
                    idx = options.index(default) if default in options else 0
                    with cols[n-1]:
                        selected = st.selectbox(
                            f'{label} (selezione manuale)', options=options, index=idx,
                            format_func=lambda v: 'Non assegnato' if v is None else f'{v:g} SPX',
                            key=f'v9_{choice}_{label}_{ref}',
                        )
                    if selected is not None:
                        chosen[label] = option_index[selected]
            wrong_order = any(
                all(f'{prefix}{n}' in chosen for n in (1,2,3)) and
                not (chosen[f'{prefix}1']['strike_spx'] < chosen[f'{prefix}2']['strike_spx'] < chosen[f'{prefix}3']['strike_spx'])
                if prefix == 'C' else
                all(f'{prefix}{n}' in chosen for n in (1,2,3)) and
                not (chosen[f'{prefix}1']['strike_spx'] > chosen[f'{prefix}2']['strike_spx'] > chosen[f'{prefix}3']['strike_spx'])
                for prefix in ('C','P')
            )
            if len({x['strike_spx'] for x in chosen.values()}) != len(chosen):
                st.error('Uno stesso strike è stato selezionato più volte. Correggere prima di usare i livelli.')
            elif wrong_order:
                st.error('Ordine non coerente: C1 < C2 < C3 e P1 > P2 > P3. Correggi i candidati.')
            else:
                ordered = []
                for label in ('C1','C2','C3','P1','P2','P3'):
                    if label in chosen:
                        item = chosen[label]
                        ordered.append({'Livello candidato': label,
                                        'Strike SPX': item['strike_spx'],
                                        'Prezzo ES': None if offset is None else item['strike_spx'] + offset,
                                        'NET GAMMA': round(item['net_gamma'], 4),
                                        'Origine': 'top 3 per intensità / selezione modificabile'})
                st.dataframe(ordered, hide_index=True, use_container_width=True)
                if offset is None:
                    st.caption('Inserisci un differenziale ES − SPX verificato per visualizzare anche i prezzi ES.')
                st.caption('La numerazione resta SPERIMENTALE. Da Serafini: C1 vicino al Gamma Flip, C2 gamma particolarmente pronunciato, C3 livello esterno vicino a 1 deviazione standard; per Put logica analoga da verificare. Qui il Gamma Flip NON è calcolato; i tre top-per-intensità sono solo una proposta modificabile.')
                with st.expander('Livelli di volatilità e copertura Gamma — confronto assistito v16', expanded=False):
                    st.info('Metodo Cash 350 verificato separatamente in TradingView: deviazione standard delle 350 chiusure M15 RTH, fissata sulla sessione precedente. Inserisci qui i valori ES UP/DOWN dell’indicatore già validato; non li ricalcoliamo né li stimiamo.')
                    if offset is None:
                        st.warning('Inserisci prima il differenziale ES−SPX per confrontare valori tutti sul future ES.')
                    else:
                        left_v, right_v = st.columns(2)
                        with left_v:
                            vol_hi = st.number_input('Soglia superiore (+1σ) ES, se disponibile', min_value=0.0, value=0.0, step=0.25, key=f'v14_volhi_{choice}')
                        with right_v:
                            vol_lo = st.number_input('Soglia inferiore (−1σ) ES, se disponibile', min_value=0.0, value=0.0, step=0.25, key=f'v14_vollo_{choice}')
                        if vol_hi > vol_lo > 0:
                            st.dataframe(gamma_comparison(chosen, offset, vol_hi, vol_lo), hide_index=True, use_container_width=True)
                            st.caption('Differenze mostrate senza spostare automaticamente C3/P3: i livelli esterni devono restare legati alla struttura opzionale.')
                        elif vol_hi or vol_lo:
                            st.warning('Per il confronto inserisci entrambe le soglie, con superiore maggiore dell’inferiore.')
                        st.markdown('**Buffer di copertura 10/15 — contesto della seduta PRECEDENTE**')
                        st.caption('Risposta Serafini 19/10: 15 se VIX in backwardation; 15 dopo forte ribasso (>1%) o seduta negativa con range >1%; altrimenti 10 nei casi ordinari entro ±1%. Non è una previsione automatica del VIX.')
                        choice_buffer = st.radio('Criterio buffer', ['Manuale 10', 'Manuale 15', 'Assistito (contesto inserito)'], horizontal=True, key=f'v15_buffer_choice_{choice}')
                        use_buffer = 10 if choice_buffer == 'Manuale 10' else 15 if choice_buffer == 'Manuale 15' else None
                        if choice_buffer == 'Assistito (contesto inserito)':
                            b1, b2, b3 = st.columns(3)
                            with b1:
                                prior_change = st.number_input('Variazione close/close precedente (%)', value=0.0, step=0.1, format='%.2f', key=f'v15_change_{choice}', help='Inserire la variazione percentuale della seduta PRECEDENTE, non quella odierna.')
                            with b2:
                                prior_range = st.number_input('Range massimo−minimo precedente (%)', min_value=0.0, value=0.0, step=0.1, format='%.2f', key=f'v15_range_{choice}', help='(Massimo−minimo)/prezzo di riferimento ×100. Indicare 0 se non disponibile; il risultato non è una verifica del VIX.')
                            with b3:
                                vix_status = st.selectbox('VIX term structure', ['Non verificata', 'Backwardation', 'Non backwardation'], key=f'v15_vix_{choice}')
                            use_buffer, reason = suggest_buffer(prior_change, prior_range if prior_range > 0 else None,
                                True if vix_status == 'Backwardation' else False if vix_status == 'Non backwardation' else None)
                            st.caption(reason)
                        if use_buffer is None:
                            st.warning('Buffer non determinato con affidabilità: usa la selezione manuale dopo aver verificato il contesto.')
                        else:
                            st.dataframe(breakout_reference(chosen, offset, use_buffer), hide_index=True, use_container_width=True)
                            st.caption(f'Buffer selezionato: **{use_buffer} punti**. Accettazione solo con CHIUSURA ES 5m oltre la soglia. Livelli indicativi, non ordini; non spiegano automaticamente 7.724 del 19/10.')
                # Sezione separata: interpretazione della struttura, mai inferenza
                # automatica sul verso delle singole transazioni.
                with st.expander('Struttura opzionale — 2 Vertical + Strangle (SPERIMENTALE v13)', expanded=True):
                    st.warning('Schema di lettura dalle slide: NON è una prova di opzioni comprate o vendute. '
                               'Il NET GAMMA aggregato non identifica il lato effettivo delle operazioni.')
                    st.caption('Prima seleziona/correggi C1–C3 e P1–P3 sopra. '
                               'I ruoli qui sotto rappresentano la struttura didattica descritta da Serafini, '
                               'non un riconoscimento certo del vertical principale.')
                    structure = build_structure(chosen, offset)
                    if structure is None:
                        st.info('Per visualizzare la struttura completa, assegna sei livelli distinti e ordinati.')
                    else:
                        role_rows, measures = structure
                        st.dataframe(role_rows, use_container_width=True, hide_index=True)
                        render_payoff_charts(chosen, offset)
                        left, right = st.columns(2)
                        with left:
                            st.markdown('**Scenario rialzista — Vertical Call**')
                            st.write('Call acquistata: C1 · Call venduta che finanzia: C2')
                            st.write('Target principale ipotizzato: **C2** · target esteso: **C3**')
                        with right:
                            st.markdown('**Scenario ribassista — Vertical Put**')
                            st.write('Put acquistata: P1 · Put venduta che finanzia: P2')
                            st.write('Target principale ipotizzato: **P2** · target esteso: **P3**')
                        st.caption('Ampiezze strike SPX (uguali in punti ES con differenziale costante): '
                                   + ' · '.join(f'{k}: {v:g}' for k,v in measures.items()))
                        with st.expander('Range asiatico — confronto manuale facoltativo'):
                            st.caption('Inserisci i massimi/minimi osservati sul future ES. '
                                       'Nessun dato asiatico è recuperato automaticamente e non vengono generati segnali.')
                            asiatic = st.checkbox('Ho i livelli del range asiatico', key=f'v13_asia_on_{choice}')
                            if asiatic:
                                ah, al = st.columns(2)
                                with ah:
                                    hi = st.number_input('Massimo asiatico ES', value=0.0, step=0.25,
                                                         key=f'v13_asia_hi_{choice}')
                                with al:
                                    lo = st.number_input('Minimo asiatico ES', value=0.0, step=0.25,
                                                         key=f'v13_asia_lo_{choice}')
                                if offset is None:
                                    st.info('Per confrontare il range ES con gli strike inserisci prima il differenziale ES–SPX.')
                                elif hi <= lo or lo <= 0:
                                    st.warning('Inserisci un massimo maggiore del minimo e valori positivi.')
                                else:
                                    st.write(f'Range asiatico ES: **{lo:g}–{hi:g}** ({hi-lo:g} punti)')
                                    for r in role_rows:
                                        es = r['Prezzo ES']
                                        where = ('sotto' if es < lo else 'sopra' if es > hi else 'dentro')
                                        st.caption(f"{r['Livello']} ({es:g}) è {where} il range asiatico")
                        st.caption('Il superamento dei livelli esterni richiede lettura del contesto e conferme; '
                                   'qui non si calcolano segnali, volatilità attesa o probabilità.')
            st.download_button('Scarica graduatoria barre NET GAMMA 0DTE (CSV)',
                               candidate_csv(candidate_0dte, offset),
                               file_name=f'Candidati_Top3_Gamma_{day.isoformat()}_{choice}.csv',
                               mime='text/csv', key=f'v9_candidates_{choice}')
    st.subheader('Esporta i due profili su TradingView')
    st.caption('Il Pine Script disegna 0DTE vicino alle candele e Weekly più a destra, come nella slide 139. I prezzi ES sono SPX + differenziale inserito qui; il NET GAMMA rimane invariato.')
    if offset is None:
        st.warning('Per esportare sul grafico del future ES inserisci prima un differenziale ES − SPX verificato. Non assumiamo zero automaticamente.')
    elif not (computed_profiles.get('0DTE', {}).get('rows') and computed_profiles.get('Weekly', {}).get('rows')):
        st.warning('Esportazione non disponibile: almeno uno dei due profili non ha dati.')
    else:
        pine = make_pine(computed_profiles['0DTE'], computed_profiles['Weekly'], offset, day.isoformat(), timestamp)
        st.download_button('Scarica Pine Script TradingView — 0DTE + Weekly', data=pine.encode('utf-8'), file_name=f'Serafini_NET_GAMMA_ES_{day.isoformat()}.pine', mime='text/plain', key=f'pine_both_{choice}')
        with st.expander('Mostra codice Pine da copiare e incollare'):
            st.code(pine, language='pine')
        st.caption('TradingView → Pine Editor → Nuovo indicatore → incolla il codice → Salva → Aggiungi al grafico ES. Per vedere i due profili usa spazio libero a destra del grafico. Il codice è uno snapshot statico, non scarica opzioni in TradingView.')
    st.caption('NET GAMMA(K) = (OI Call + VOL Call) × Gamma Call − (OI Put + VOL Put) × Gamma Put. Nessun fattore monetario aggiunto.')

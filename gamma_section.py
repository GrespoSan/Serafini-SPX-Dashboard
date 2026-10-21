"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import gzip
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from net_gamma import profile, profile_csv, read_snapshot
from volatility_levels import weekly_expiry
from gamma_compare import compare_profiles
from gamma_brief import select_key_levels, operational_summary, transition_zones
from pine_export import make_pine
from operational_levels import find_candidates, proposed_levels, candidate_csv
from option_structure import build_structure
from payoff_charts import render_payoff_charts
from asia_range import download_asia_range
from es_spx_basis import download_and_calculate
from decimal import Decimal, ROUND_HALF_UP
from html import escape


def _has_usable_gamma(snapshot):
    """Qualita' minima: almeno un contratto con gamma finito non nullo.

    Controllo sul file originale, indipendente dalla formula NET GAMMA.
    """
    import math
    for option in snapshot.get('data', {}).get('options', []):
        try:
            gamma = float(option.get('gamma') or 0)
            if math.isfinite(gamma) and gamma != 0:
                return True
        except (ValueError, TypeError, OverflowError):
            pass
    return False


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


def _acquisition_label(store, week, logs, file_name):
    """L'etichetta usa esclusivamente il registro del download, NON il timestamp Cboe.

    Nessun orario viene inferito dal timestamp sorgente, che non dichiara il fuso.
    """
    path = f'{week}/snapshots/{file_name}'
    matching = []
    for entry in logs:
        try:
            item = json.loads(store.download(f"{week}/logs/{entry['name']}"))
            if item.get('file') != path or item.get('status') not in ('NUOVO', 'INVARIATO'):
                continue
            downloaded = item.get('download_rome') or item.get('download_utc')
            if not downloaded:
                continue
            dt = datetime.fromisoformat(str(downloaded).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                continue
            dt = dt.astimezone(ZoneInfo('Europe/Rome'))
            slot = str(item.get('scheduled_slot', '')).strip()
            if slot == '30 7 * * 1-5':
                label = 'Mattina 07:30'
            elif slot == '46 15 * * 1-5':
                label = 'Pomeriggio 15:46'
            elif slot == '50 15 * * 1-5':
                label = 'Pomeriggio 15:50'
            elif slot == '30 15 * * 1-5':
                label = 'Pomeriggio 15:30 (storico)'
            elif slot == '45 7 * * 1-5':
                label = 'Recupero mattina 07:45'
            elif slot == '45 15 * * 1-5':
                label = 'Recupero pomeriggio 15:45'
            elif slot.lower() in ('', 'manuale', 'manual', 'test', 'workflow_dispatch'):
                label = f'Manuale {dt:%H:%M}'
            else:
                label = f'Download {dt:%H:%M}'
            matching.append((dt, label))
        except (KeyError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
            continue
    if not matching:
        return 'Rilevamento non identificato'
    # Quando lo stesso file appare in più log, preferire il primo download effettivo.
    return min(matching, key=lambda r: r[0])[1]



def _snapshot_download_clock(store, week, logs, file_name):
    """Ora effettiva di download a Roma dai log; NON dal timestamp Cboe.

    Restituisce None se il file non ha registri attendibili.
    """
    expected_path = f'{week}/snapshots/{file_name}'
    clock_times = []
    for entry in logs:
        try:
            item = json.loads(store.download(f"{week}/logs/{entry['name']}"))
            if item.get('file') != expected_path or item.get('status') not in ('NUOVO', 'INVARIATO'):
                continue
            raw = item.get('download_rome') or item.get('download_utc')
            if not raw:
                continue
            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                continue
            clock_times.append(stamp.astimezone(ZoneInfo('Europe/Rome')))
        except (ValueError, TypeError, KeyError, OverflowError, OSError, RuntimeError):
            continue
    return min(clock_times).strftime('%H:%M') if clock_times else None

def render_comparison(store, week, snaps, logs):
    """Confronto autonomo; usa il contesto del profilo singolo selezionato."""
    ctx = st.session_state.get(f'gamma_compare_context_{week}')
    if not ctx:
        st.info('Apri prima la sezione NET GAMMA 0DTE / Weekly per selezionare lo snapshot.')
        return
    day, ref, offset, weekly_exp = (ctx['day'], ctx['ref'], ctx['offset'], ctx['weekly_exp'])
    names = sorted((obj['name'] for obj in snaps), reverse=True)
    st.subheader('Confronto NET GAMMA — rilevamenti della stessa giornata')
    same_day = sorted(n for n in names if n.startswith(f'SPX_CBOE_{day:%Y%m%d}_'))
    # Escludere dal confronto gli snapshot senza Gamma, compresi gli storici
    # precedenti all'introduzione del controllo qualita' nel downloader.
    valid_names = []
    rejected = []
    for candidate in same_day:
        try:
            candidate_data = read_snapshot(store.download(f'{week}/snapshots/{candidate}'))
            if _has_usable_gamma(candidate_data):
                valid_names.append(candidate)
            else:
                rejected.append(candidate)
        except Exception:
            rejected.append(candidate)
    if rejected:
        st.warning(f'{len(rejected)} snapshot esclusi dal confronto: Gamma assenti o file non leggibili. '
                   'Restano conservati nell’archivio, con la segnalazione nella colonna Qualità dati.')
    same_day = valid_names
    if len(same_day) < 2:
        st.info('Servono due snapshot validi della stessa giornata per il confronto NET GAMMA.')
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            first_name = st.selectbox('Snapshot iniziale (es. 15:46)', same_day[:-1], index=0, key=f'v19_first_{day}')
        later = [n for n in same_day if n > first_name]
        if not later:
            st.info('Non ci sono snapshot successivi per il confronto.')
            return
        with col_b:
            second_name = st.selectbox('Snapshot finale (anche manuale)', later, index=len(later)-1,
                                       key=f'v19_second_{day}_{first_name}')
        first_clock = _snapshot_download_clock(store, week, logs, first_name)
        second_clock = _snapshot_download_clock(store, week, logs, second_name)
        first_label = first_clock or 'Iniziale (ora n.d.)'
        second_label = second_clock or 'Finale (ora n.d.)'
        interval_label = f'{first_label} : {second_label}'
        if first_clock is None or second_clock is None:
            st.caption('Orario di download non presente nei registri per almeno uno snapshot: non viene ricavato dal timestamp Cboe.')
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
                    st.info('Non ci sono strike confrontabili per la scadenza selezionata. Verificare scadenza e riferimento SPX.')
                else:
                    import plotly.graph_objects as go
                    # Grafico principale: solo otto concentrazioni, colori per ORARIO.
                    # Grafico di transizione separato: colori per SEGNO NET (rosso/verde).
                    def _price_label(r):
                        return (f"{r['Prezzo ES']:,.0f}".replace(',', '.')
                                if r['Prezzo ES'] is not None else f"{r['Strike SPX']:g} SPX")

                    # Grafico UNICO: otto concentrazioni principali e, nel 0DTE,
                    # tutti gli strike presenti nella tabella delle zone di transizione.
                    # Il prezzo e' una categoria unica, quindi PRE e POST coincidono
                    # esattamente per centro e altezza: niente barre disallineate.
                    indexed = {float(r['Strike SPX']): r for r in main_levels}
                    zones = transition_zones(comparisons) if selected_profile == '0DTE' else []
                    for zone in zones:
                        for rec in zone['rows']:
                            indexed[float(rec['Strike SPX'])] = rec
                    ordered = sorted(indexed.values(), key=lambda r: float(r['Strike SPX']))
                    def _comparison_label(rec):
                        a = float(rec['NET mattina'])
                        b = float(rec['NET pomeriggio'])
                        if a < 0 < b:
                            description = 'PUT → CALL'
                        elif a > 0 > b:
                            description = 'CALL → PUT'
                        elif abs(b) > abs(a):
                            description = 'CALL ↑' if b > 0 else 'PUT ↑'
                        elif abs(b) < abs(a):
                            description = 'CALL ↓' if b > 0 else 'PUT ↓'
                        else:
                            description = 'invariato'
                        return f'{_price_label(rec)}  ·  {description}'

                    levels = [_comparison_label(rec) for rec in ordered]
                    old_values = [float(rec['NET mattina']) for rec in ordered]
                    new_values = [float(rec['NET pomeriggio']) for rec in ordered]
                    actual_colors = ['#22C55E' if val >= 0 else '#EF4444'
                                     for val in new_values]
                    previous_colors = ['#22C55E' if val >= 0 else '#EF4444'
                                       for val in old_values]

                    fig = go.Figure()
                    # Le barre Plotly trasparenti possono sovrascrivere il bordo
                    # quando il valore successivo copre il precedente.
                    # Asse Y numerico: disegniamo la sagoma come linee Scatter
                    # SOPRA le barre. Questo assicura stessa altezza, stesso
                    # centro e visibilita' completa del precedente.
                    row_y = list(range(len(levels)))
                    bar_height = 0.64
                    fig.add_bar(
                        name=f'DOPO {second_label}',
                        y=row_y, x=new_values, orientation='h',
                        width=bar_height, marker=dict(color=actual_colors),
                        customdata=[[levels[i], old_values[i]] for i in row_y],
                        hovertemplate=(
                            '%{customdata[0]}<br>DOPO: %{x:+.2f}'
                            '<br>PRIMA: %{customdata[1]:+.2f}<extra></extra>'
                        ),
                    )
                    half_height = bar_height / 2
                    for idx, before in enumerate(old_values):
                        top, bottom = idx + half_height, idx - half_height
                        fig.add_trace(go.Scatter(
                            x=[0, before, before, 0, 0],
                            y=[bottom, bottom, top, top, bottom],
                            mode='lines',
                            line=dict(color=previous_colors[idx], width=3),
                            fill='none',
                            name=f'PRIMA {first_label}',
                            showlegend=False,
                            hoverinfo='skip',
                        ))
                    # Piccolo marcatore del valore PRIMA per il tooltip,
                    # indipendente dall'ordine di rendering delle barre.
                    fig.add_trace(go.Scatter(
                        x=old_values, y=row_y, mode='markers',
                        marker=dict(size=7, symbol='line-ns-open',
                                    color=previous_colors, line=dict(width=2)),
                        customdata=levels, showlegend=False,
                        hovertemplate=('%{customdata}<br>PRIMA: %{x:+.2f}'
                                       '<extra></extra>'),
                    ))
                    extent = max(abs(val) for val in old_values + new_values)
                    extent = max(extent, 1.0)
                    st.markdown(f'**Confronto NET GAMMA {selected_profile} — {day:%d/%m/%Y}**')
                    st.caption(
                        f'PRIMA: {first_label} Roma = contorno; '
                        f'DOPO: {second_label} Roma = barra piena. '
                        'Verde NET positivo, rosso NET negativo. '
                        'I due profili usano la stessa scala e la stessa altezza per strike.'
                    )
                    fig.update_layout(
                        title='Evoluzione NET GAMMA — prima (contorno) / dopo (pieno)',
                        barmode='overlay', template='plotly_dark',
                        height=max(510, 48 * len(ordered) + 125),
                        margin=dict(l=18, r=55, t=57, b=55),
                        showlegend=False,
                        xaxis=dict(title='NET GAMMA',
                                   range=[-1.10 * extent, 1.10 * extent],
                                   zeroline=True, zerolinecolor='#CBD5E1'),
                        yaxis=dict(title=None, automargin=True,
                                   tickmode='array', tickvals=row_y,
                                   ticktext=levels,
                                   range=[-0.75, len(levels) - 0.25]),
                    )
                    fig.add_vline(x=0, line_color='#CBD5E1', line_width=1)
                    st.plotly_chart(
                        fig, use_container_width=True,
                        key=f'v41_one_gamma_{selected_profile}_{first_name}_{second_name}',
                    )
                    st.caption(
                        'Ogni riga indica lo stesso strike nei due rilevamenti. '
                        'Gli strike supplementari del 0DTE derivano dalle zone '
                        'di transizione della tabella. Il segno NET GAMMA non prova '
                        'posizioni CALL/PUT effettivamente comprate o vendute.'
                    )

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
                                    return 'Passaggio da CALL a PUT'
                                if a < 0 < b:
                                    return 'Passaggio da PUT a CALL'
                                if a == 0 or b == 0:
                                    return 'Passaggio per zero' if a != b else 'Invariato'
                                if abs(b) > abs(a) + 1e-8:
                                    return 'Aumento di CALL' if b > 0 else 'Aumento di PUT'
                                if abs(b) < abs(a) - 1e-8:
                                    return 'Riduzione di CALL' if b > 0 else 'Riduzione di PUT'
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
                                f'<th style="padding:10px 12px">{escape(interval_label)}</th>'
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
                            st.caption(f'I valori indicano due rilevazioni distinte ({interval_label}), non si sommano.')
                            def _price(r):
                                return (f"{r['Prezzo ES']:,.0f}".replace(',', '.') if r['Prezzo ES'] is not None
                                        else f"SPX {r['Strike SPX']:g}")
                            def _colored(v):
                                color = '#22c55e' if v > 0 else '#ef4444' if v < 0 else '#d1d5db'
                                value = f"{v:+.2f}".replace('.', ',').replace('-', '−')
                                return f'<span style="color:{color};font-weight:650;font-variant-numeric:tabular-nums">{value}</span>'
                            def _description(old, new):
                                if old > 0 > new:
                                    return 'Passaggio da CALL a PUT'
                                if old < 0 < new:
                                    return 'Passaggio da PUT a CALL'
                                if old == 0 or new == 0:
                                    return 'Valore zero / cambio di segno' if old != new else 'Invariato'
                                if abs(new) > abs(old) + 1e-8:
                                    return 'Aumento di CALL' if new > 0 else 'Aumento di PUT'
                                if abs(new) < abs(old) - 1e-8:
                                    return 'Riduzione di CALL' if new > 0 else 'Riduzione di PUT'
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
                                              f'<thead><tr style="background:rgba(125,125,125,0.13)"><th style="padding:10px 12px">Livello ES</th><th style="padding:10px 12px">{escape(interval_label)}</th><th style="padding:10px 12px">Variazione</th></tr></thead>'
                                              '<tbody>' + ''.join(body) + '</tbody></table></div>')
                                st.markdown(table_html, unsafe_allow_html=True)
                                st.markdown(f'**Sintesi operativa:** {_transition_summary(rows, zone["direction"])}')
                        else:
                            st.caption('Nessun cambiamento di segno netto nei livelli comuni ai due snapshot.')

                    st.caption('Grafico principale: 8 concentrazioni. Evoluzione zona: tutti i livelli della tabella, anche quelli senza cambio di segno. Nessun segnale operativo automatico.')
                    if stats['solo_mattina'] or stats['solo_pomeriggio']:
                        st.caption(f"Strike confrontati: {stats['comuni']} · non comuni: "
                                   f"{stats['solo_mattina'] + stats['solo_pomeriggio']}.")
        except Exception as exc:
            st.error(f'Confronto non disponibile: {type(exc).__name__}: {str(exc)[:220]}')



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
    acquisition_label = _acquisition_label(store, week, logs, choice)
    download_clock = _snapshot_download_clock(store, week, logs, choice)
    st.caption(f'Rilevamento selezionato: {acquisition_label} · Timestamp Cboe: {timestamp} · file {choice}')
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
    attempt_key = f'basis_auto_attempted_{day.isoformat()}'
    if 'auto_basis_results' not in st.session_state:
        st.session_state.auto_basis_results = {}

    def _apply_auto_basis():
        result = _auto_basis(day)
        st.session_state.auto_basis_results[day.isoformat()] = result
        st.session_state[basis_key] = str(int(Decimal(str(result['basis'])).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP)))
        return result

    # Una sola acquisizione automatica per giorno e sessione Streamlit.
    # Non sovrascrivere un valore gia' modificato manualmente.
    if attempt_key not in st.session_state:
        st.session_state[attempt_key] = True
        if not str(st.session_state.get(basis_key, '')).strip():
            try:
                with st.spinner('Calcolo automatico differenziale ES − SPX...'):
                    _apply_auto_basis()
            except Exception as exc:
                st.session_state.auto_basis_results[day.isoformat()] = None
                st.session_state[f'basis_auto_error_{day.isoformat()}'] = str(exc)[:250]

    # v41: Streamlit puo' conservare il risultato automatico ma lasciare vuoto
    # il widget dopo un cambio snapshot/rerun. Riallineiamo lo stato PRIMA
    # dell'istanziazione del text_input, senza sovrascrivere valori non vuoti
    # (anche se modificati manualmente dall'utente).
    cached_basis = st.session_state.auto_basis_results.get(day.isoformat())
    if cached_basis is not None and not str(st.session_state.get(basis_key, '')).strip():
        st.session_state[basis_key] = str(int(Decimal(str(cached_basis['basis'])).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP)))

    col1, col2, col3 = st.columns([1.15, 1.45, 0.65], gap='small')
    with col1:
        ref = st.number_input('Chiusura SPX precedente', min_value=0.01,
                              value=float(previous or snap['data']['current_price']),
                              step=0.25, format='%.4f',
                              help='Riferimento per i 50 strike. Verificare con la chiusura cash ufficiale.')
    with col2:
        offset_txt = st.text_input('Differenziale ES − SPX applicato in automatico e modificabile',
                                   key=basis_key,
                                   placeholder='Es. 47',
                                   help='Calcolato automaticamente alla prima apertura; puoi correggerlo. Vuoto = grafico SPX.')
    with col3:
        # Allinea il pulsante all'altezza dei campi, anziché alle intestazioni.
        st.markdown('<div style="height: 29px"></div>', unsafe_allow_html=True)
        if st.button('Ricalcola ES − SPX', key=f'auto_basis_{day}', use_container_width=True):
            try:
                with st.spinner('Acquisizione e confronto barre ES/SPX a 15 minuti...'):
                    _apply_auto_basis()
                st.session_state.pop(f'basis_auto_error_{day.isoformat()}', None)
                st.rerun()
            except Exception as exc:
                st.warning(f'Ricalcolo non disponibile: {str(exc)[:250]}. Mantengo il valore inserito.')
    if st.session_state.get(f'basis_auto_error_{day.isoformat()}'):
        st.caption('Calcolo automatico non disponibile: ' +
                   st.session_state[f'basis_auto_error_{day.isoformat()}'] +
                   '. Puoi inserire manualmente il differenziale.')
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
    st.session_state[f'gamma_compare_context_{week}'] = {
        'day': day, 'ref': ref, 'offset': offset, 'weekly_exp': weekly_exp,
    }
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
        capture_info = (f'{acquisition_label} · download ore {download_clock} Roma'
                        if download_clock else f'{acquisition_label} · ora download non disponibile')
        st.subheader(f'{title} — RILEVAMENTO {day:%d/%m/%Y} · {capture_info}')
        st.caption(f'Scadenza opzioni: {expiry:%d/%m/%Y} · Timestamp originale Cboe: {timestamp} (fuso non dichiarato)')
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
            title=f'{title} | {day:%d/%m/%Y} | {capture_info}<br><sup>NET GAMMA per strike — positivo a destra / negativo a sinistra</sup>',
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
                        render_payoff_charts(chosen, offset, computed_profiles.get('0DTE'))
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
                        with st.expander('Range asiatico ES — 00:00–09:00 Roma (automatico)', expanded=False):
                            st.caption('Box del giorno dello snapshot, ore italiane; 36 barre a 15 minuti. '
                                       'Fonte Yahoo ES=F, da confrontare con il future TradingView. '
                                       'Per gli snapshot mattutini il range completo è disponibile solo dopo le 09:00.')
                            @st.cache_data(ttl=900, show_spinner=False)
                            def _load_asia(target_day):
                                return download_asia_range(target_day)
                            auto_asia = None
                            if st.button('Calcola massimo/minimo ES automaticamente', key=f'asia_fetch_v27_{choice}'):
                                try:
                                    auto_asia = _load_asia(day)
                                    st.session_state[f'asia_result_v27_{day}'] = auto_asia
                                    st.session_state.pop(f'asia_error_v27_{day}', None)
                                except Exception as exc:
                                    st.session_state.pop(f'asia_result_v27_{day}', None)
                                    st.session_state[f'asia_error_v27_{day}'] = str(exc)
                            auto_asia = st.session_state.get(f'asia_result_v27_{day}')
                            if auto_asia:
                                hi, lo = auto_asia['high'], auto_asia['low']
                                st.success(f"Range asiatico ES {day:%d/%m/%Y}: **{lo:,.2f} – {hi:,.2f}** "
                                           f"(ampiezza {hi-lo:,.2f} punti, {auto_asia['bars']}/36 barre)")
                                st.caption(auto_asia['source'])
                            else:
                                msg = st.session_state.get(f'asia_error_v27_{day}')
                                if msg:
                                    st.warning(msg)
                                st.caption('Se non sono disponibili tutte le barre, nessun range automatico viene utilizzato.')
                            if st.checkbox('Usa valori ES manuali (verifica TradingView)',
                                           key=f'asia_manual_v27_{choice}'):
                                ah, al = st.columns(2)
                                with ah:
                                    hi = st.number_input('Massimo ES 00:00–09:00', min_value=0.0,
                                                         value=float(auto_asia['high']) if auto_asia else 0.0,
                                                         step=0.25, key=f'asia_hi_v27_{choice}')
                                with al:
                                    lo = st.number_input('Minimo ES 00:00–09:00', min_value=0.0,
                                                         value=float(auto_asia['low']) if auto_asia else 0.0,
                                                         step=0.25, key=f'asia_lo_v27_{choice}')
                                if hi <= lo or lo <= 0:
                                    st.warning('Inserisci un massimo maggiore del minimo e valori ES positivi.')
                                    hi = lo = None
                            elif auto_asia is None:
                                hi = lo = None
                            if hi is not None and lo is not None:
                                if offset is None:
                                    st.info('Per confrontare i livelli Gamma con il range ES inserisci il differenziale ES−SPX.')
                                else:
                                    for r in role_rows:
                                        es = r['Prezzo ES']
                                        where = ('sotto' if es < lo else 'sopra' if es > hi else 'dentro')
                                        st.caption(f"{r['Livello']} ({es:,.2f} ES): {where} il range")
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
        pine = make_pine(computed_profiles['0DTE'], computed_profiles['Weekly'], offset, day.isoformat(), timestamp, acquisition_label)
        st.download_button('Scarica Pine Script TradingView — 0DTE + Weekly', data=pine.encode('utf-8'), file_name=f'Serafini_NET_GAMMA_ES_{day.isoformat()}.pine', mime='text/plain', key=f'pine_both_{choice}')
        with st.expander('Mostra codice Pine da copiare e incollare'):
            st.code(pine, language='pine')
        st.caption('TradingView → Pine Editor → Nuovo indicatore → incolla il codice → Salva → Aggiungi al grafico ES. Per vedere i due profili usa spazio libero a destra del grafico. Il codice è uno snapshot statico, non scarica opzioni in TradingView.')
    st.caption('NET GAMMA(K) = (OI Call + VOL Call) × Gamma Call − (OI Put + VOL Put) × Gamma Put. Nessun fattore monetario aggiunto.')

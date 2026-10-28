"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import gzip
import json
import math
from datetime import date, timedelta, time
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from net_gamma import profile, profile_csv, read_snapshot
from feed_quality import timestamp_state
from volatility_levels import weekly_expiry
from gamma_compare import compare_profiles
from volume_audit import compare_volume_profiles, inspect_raw_expiry, raw_transition_diagnostic
from gamma_brief import select_key_levels, operational_summary, transition_zones
from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_sources import load_export_profiles
from gamma_scale import profile_gamma_bounds, gamma_components
from serafini_sigma import serafini_cash_levels, add_sigma_markers
from operational_levels import find_candidates, proposed_levels, candidate_csv
from option_structure import build_structure
from payoff_charts import render_payoff_charts
from asia_range import download_asia_range
from es_spx_basis import download_and_calculate
from decimal import Decimal, ROUND_HALF_UP
from html import escape


@st.cache_data(ttl=300, max_entries=90, show_spinner=False)
def _snapshot_cboe_stamp(week, file_name, _store):
    """Un solo timestamp storico aggiuntivo, cache 5 minuti, solo lettura."""
    item = read_snapshot(_store.download(f"{week}/snapshots/{file_name}"))
    return item.get("timestamp")


@st.cache_data(ttl=180, max_entries=50, show_spinner=False)
def _pine_export_data(week, day, logs_count, _store, _logs):
    """Cache breve per non riscaricare i 3 snapshot raw a ogni rerun."""
    return load_export_profiles(_store, week, day, _logs)


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
            elif slot == '30 20 * * 1-5':
                label = 'Sera 20:30'
            elif slot == '30 13 * * 1-5':
                label = '13:30 (studio storico)'
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


@st.cache_data(ttl=300, show_spinner=False, max_entries=32)
def _morning_asia_candidates(week, target, log_names, _store):
    """Individua C1/P1 *sperimentali* esclusivamente dal Cboe delle 07:30.

    Si basa sul log del download ufficiale, con verifica della data/ora
    di acquisizione: un download dopo le 09:00 non è valido.
    Non utilizza lo snapshot selezionato né dati successivi.
    """
    from net_gamma import profile, read_snapshot

    rome = ZoneInfo("Europe/Rome")
    eligible = []
    for log_name in log_names:
        try:
            entry = json.loads(_store.download(f"{week}/logs/{log_name}"))
            if (entry.get("scheduled_slot") != "30 7 * * 1-5"
                    or entry.get("rome_date") != target.isoformat()
                    or entry.get("status") not in ("NUOVO", "INVARIATO")):
                continue
            name = str(entry.get("file") or "")
            if not name.startswith(f"{week}/snapshots/SPX_CBOE_{target:%Y%m%d}_"):
                continue
            raw = entry.get("download_rome") or entry.get("download_utc")
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                continue
            dt = dt.astimezone(rome)
            if dt.date() == target and dt.time() <= time(9, 0):
                eligible.append((dt, name))
        except (KeyError, ValueError, TypeError, RuntimeError, OSError, OverflowError):
            continue

    for dt, source in sorted(eligible):
        try:
            snap = read_snapshot(_store.download(source))
            reference = float(snap["data"].get("prev_day_close"))
            if not math.isfinite(reference) or reference <= 0:
                continue
            rows = profile(snap, target, reference).get("rows") or []
            if len(rows) != 50:
                continue
            proposed = proposed_levels(find_candidates(rows, reference))
            if not all(k in proposed for k in ("C1", "C2", "C3", "P1", "P2", "P3")):
                continue
            c1 = float(proposed["C1"]["strike_spx"])
            p1 = float(proposed["P1"]["strike_spx"])
            if not (p1 < reference < c1):
                continue
            return {
                "source": source.rsplit("/", 1)[-1],
                "download_rome": dt.isoformat(timespec="seconds"),
                "reference_spx": reference,
                "c1_spx": c1, "p1_spx": p1,
                "method": "3 massime intensità per lato — NON LIVELLI CONFERMATI",
            }
        except (KeyError, ValueError, TypeError, RuntimeError, OSError, OverflowError):
            continue
    return None


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
        # Defaults per orari effettivi di acquisizione (registri Europe/Rome).
        # Scelta manuale successiva sempre preservata.
        clock_by_name = {name: _snapshot_download_clock(store, week, logs, name)
                         for name in same_day}
        def closest_slot(candidates, hour, minute, tolerance=20):
            target = hour * 60 + minute
            matches = []
            for name in candidates:
                clock = clock_by_name.get(name)
                if not clock:
                    continue
                try:
                    hh, mm = map(int, clock[:5].split(':'))
                    delta = abs(hh * 60 + mm - target)
                    if delta <= tolerance:
                        matches.append((delta, name))
                except (ValueError, TypeError):
                    continue
            return min(matches)[1] if matches else None

        now_rome = datetime.now(ZoneInfo('Europe/Rome'))
        allow_afternoon = (day < now_rome.date()
                           or (day == now_rome.date() and
                               (now_rome.hour, now_rome.minute) >= (15, 50)))
        default_first = closest_slot(same_day[:-1], 7, 30) or same_day[0]
        first_key = f'v19_first_{day}'
        if st.session_state.get(first_key) not in same_day[:-1]:
            st.session_state[first_key] = default_first
        col_a, col_b = st.columns(2)
        first_clock_preview = clock_by_name.get(st.session_state[first_key])
        first_caption = first_clock_preview or 'ora non disponibile'
        with col_a:
            first_name = st.selectbox(
                f'Snapshot iniziale — download {first_caption} Roma',
                same_day[:-1], key=first_key,
            )
        later = [n for n in same_day if n > first_name]
        if not later:
            st.info('Non ci sono snapshot successivi per il confronto.')
            return
        second_key = f'v19_second_{day}_{first_name}'
        default_second = (closest_slot(later, 15, 50) if allow_afternoon else None)
        if default_second is None:
            default_second = closest_slot(later, 13, 30)
        if default_second is None:
            default_second = later[-1]
        if st.session_state.get(second_key) not in later:
            st.session_state[second_key] = default_second
        second_clock_preview = clock_by_name.get(st.session_state[second_key])
        second_caption = second_clock_preview or 'ora non disponibile'
        with col_b:
            second_name = st.selectbox(
                f'Snapshot finale — download {second_caption} Roma',
                later, key=second_key,
            )
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
            elif old_prof['expiry'] != new_prof['expiry']:
                st.warning('Scadenze diverse: i profili non sono confrontabili. '
                           'Non attribuire a flussi Gamma il cambio di contratto.')
            else:
                volume_check = compare_volume_profiles(
                    old_prof['rows'], new_prof['rows'],
                    old_expiry=old_prof['expiry'], new_expiry=new_prof['expiry'])
                selected_strikes = {float(x['strike_spx']) for x in old_prof['rows']}
                selected_strikes &= {float(x['strike_spx']) for x in new_prof['rows']}
                raw_old = inspect_raw_expiry(older['data']['options'], expiry, selected_strikes)
                raw_new = inspect_raw_expiry(newer['data']['options'], expiry, selected_strikes)
                def clock_as_iso(clock):
                    if not clock:
                        return None
                    return datetime.combine(day, time.fromisoformat(clock),
                                            tzinfo=ZoneInfo('Europe/Rome')).isoformat()
                raw_check = raw_transition_diagnostic(
                    raw_old, raw_new, clock_as_iso(first_clock), clock_as_iso(second_clock))
                if raw_check.get('possible_new_session_volume_reset'):
                    st.info('Il calo di Volume coincide con il passaggio attraverso '
                            'l\'apertura USA delle 09:30 New York, con Open Interest '
                            'stabile: possibile reset giornaliero dei contatori. '
                            'È un\'ipotesi, non un errore Cboe dimostrato.')
                with st.expander('Diagnostica Volume originale Cboe (stessi contratti)', expanded=False):
                    st.caption(
                        f"Contratti comuni: {raw_check.get('common_contracts', 0)} · "
                        f"Volume diminuito: {raw_check.get('contracts_volume_lower', 0)} · "
                        f"Passati a zero: {raw_check.get('contracts_positive_to_zero', 0)} · "
                        f"Passati a dato mancante: {raw_check.get('contracts_become_missing', 0)}.")
                    st.dataframe(raw_check.get('example_largest_declines', []),
                                 hide_index=True, use_container_width=True)
                if volume_check['alert'] and not raw_check.get('possible_new_session_volume_reset'):
                    st.warning(
                        f"ATTENZIONE — volumi Cboe potenzialmente non omogenei: "
                        f"{selected_profile} su {volume_check['common_strikes']} strike comuni. "
                        f"Volume {volume_check['volume_initial']:.0f} → "
                        f"{volume_check['volume_final']:.0f} "
                        f"({volume_check['volume_change_pct']:+.1f}%), "
                        f"Open Interest {volume_check['oi_change_pct']:+.1f}%. "
                        "Le variazioni NET GAMMA possono riflettere anche dati Volume "
                        "non confrontabili; non dimostrano l'apertura o chiusura di posizioni."
                    )
                if volume_check['alert'] and raw_check.get('possible_new_session_volume_reset'):
                    st.caption("I volumi prima e dopo l'apertura USA non sono "
                               "direttamente confrontabili come variazione intraday "
                               "delle posizioni: probabile cambio di sessione Volume.")
                comparisons, stats = compare_profiles(old_prof, new_prof, offset)
                main_levels = select_key_levels(comparisons, count_per_sign=4, minimum_separation=5.0)
                if not comparisons:
                    st.info('Non ci sono strike confrontabili per la scadenza selezionata. Verificare scadenza e riferimento SPX.')
                else:
                    import plotly.graph_objects as go
                    # Visualizza tutti gli strike realmente comuni ai due profili
                    # 25 sopra + 25 sotto: massimo 50, nessun valore inventato.
                    # Le concentrazioni principali restano calcolate a parte per
                    # il riepilogo operativo, senza limitarne il grafico.
                    ordered = sorted(comparisons, key=lambda r: float(r['Strike SPX']))
                    if len(ordered) > 50:
                        raise ValueError('Confronto oltre i 50 strike previsti dal metodo Serafini.')
                    def _price_label(rec):
                        return (f"{rec['Prezzo ES']:,.0f}".replace(',', '.')
                                if rec['Prezzo ES'] is not None
                                else f"{rec['Strike SPX']:g} SPX")

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
                    price_values = [float(rec['Prezzo ES'] if offset is not None else rec['Strike SPX']) for rec in ordered]
                    row_y = price_values
                    gaps = [row_y[i + 1] - row_y[i] for i in range(len(row_y) - 1) if row_y[i + 1] > row_y[i]]
                    bar_height = min(gaps) * 0.64 if gaps else 3.2
                    fig.add_bar(
                        name=f'DOPO {second_label}',
                        y=row_y, x=new_values, orientation='h',
                        width=bar_height, marker=dict(color=actual_colors),
                        customdata=[[levels[i], old_values[i]] for i in range(len(row_y))],
                        hovertemplate=(
                            '%{customdata[0]}<br>DOPO: %{x:+.2f}'
                            '<br>PRIMA: %{customdata[1]:+.2f}<extra></extra>'
                        ),
                    )
                    # Plotly disegna i trace Bar sopra i trace Scatter, anche
                    # quando gli Scatter sono stati aggiunti dopo. Perciò le
                    # vecchie sagome sparivano sotto le nuove barre.
                    # Le SHAPES con layer='above' restano davvero sopra i bar.
                    half_height = bar_height / 2
                    for idx, before in enumerate(old_values):
                        center_y = row_y[idx]
                        old_border = '#86EFAC' if before >= 0 else '#FDA4AF'
                        fig.add_shape(
                            type='rect',
                            xref='x', yref='y',
                            x0=min(0.0, before), x1=max(0.0, before),
                            y0=center_y - half_height, y1=center_y + half_height,
                            fillcolor='rgba(0,0,0,0)',
                            line=dict(color=old_border, width=3, dash='dash'),
                            layer='above',
                        )
                    # Valori esatti PRIMA e DOPO sono nel tooltip della barra.
                    extent = max(abs(val) for val in old_values + new_values)
                    extent = max(extent, 1.0)
                    st.markdown(f'**Confronto NET GAMMA {selected_profile} — {day:%d/%m/%Y}**')
                    st.markdown(
                        f'### PRIMA: {first_label} Roma (contorno) - '
                        f'DOPO: {second_label} Roma (barra piena)'
                    )
                    fig.update_layout(
                        title='Evoluzione NET GAMMA — prima (contorno) / dopo (pieno)',
                        barmode='overlay', template='plotly_dark',
                        height=max(480, 22 * len(ordered) + 135),
                        margin=dict(l=18, r=55, t=57, b=55),
                        showlegend=False,
                        xaxis=dict(title='NET GAMMA',
                                   range=[-1.10 * extent, 1.10 * extent],
                                   zeroline=True, zerolinecolor='#CBD5E1'),
                        yaxis=dict(title=None, automargin=True,
                                   tickmode='array', tickvals=row_y,
                                   ticktext=levels,
                                   range=[min(row_y) - bar_height * 1.3, max(row_y) + bar_height * 1.3]),
                    )
                    fig.add_vline(x=0, line_color='#CBD5E1', line_width=1)
                    # Riferimenti grafici coerenti con il profilo singolo.
                    # Nessuna modifica a calcoli NET GAMMA o frame di confronto.
                    if offset is not None and math.isfinite(offset):
                        previous_close_es = float(ref) + offset
                        fig.add_hline(y=previous_close_es, line_dash='dash',
                                      line_width=1.5, line_color='#f0cc65')
                        for pct in (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5):
                            target = previous_close_es * (1.0 + pct / 100.0)
                            emphasis = abs(pct) == 1.0
                            fig.add_hline(y=target, line_dash='dot',
                                          line_width=1.8 if emphasis else 1.2,
                                          line_color=('rgba(205,215,230,0.62)' if emphasis
                                                      else 'rgba(195,205,220,0.46)'))
                            fig.add_annotation(
                                x=0.995, xref='paper', y=target, yref='y',
                                text=f'{pct:+.1f}%'.replace('.', ','),
                                showarrow=False, xanchor='right', yanchor='middle',
                                font=dict(color='rgba(225,232,240,0.92)', size=10),
                                bgcolor='rgba(13,17,23,0.78)', borderpad=1)
                        fig.add_annotation(
                            x=0.995, xref='paper', y=previous_close_es, yref='y',
                            text='Close prec.', showarrow=False,
                            xanchor='right', yanchor='bottom',
                            font=dict(color='#f0cc65', size=11),
                            bgcolor='rgba(13,17,23,0.85)', borderpad=2)
                        if selected_profile == '0DTE':
                            try:
                                sigma_compare = serafini_cash_levels(day)
                                add_sigma_markers(fig, sigma_compare, row_y)
                            except Exception:
                                st.caption('L90 / L350 / S350 non disponibili per questa giornata.')
                    else:
                        st.caption('Riferimenti ES non disponibili: verificare il differenziale in ANALISI NET GAMMA.')
                    st.plotly_chart(
                        fig, use_container_width=True,
                        key=f'v42_one_gamma_{selected_profile}_{first_name}_{second_name}',
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
    st.header('NET GAMMA · formula Serafini')
    if not snaps:
        st.info('Nessuno snapshot disponibile nella settimana selezionata.')
        return
    names = sorted((o['name'] for o in snaps), reverse=True)
    selection_key = f'serafini_selected_snapshot_{week}'
    if st.session_state.pop(f'spx_refresh_latest_{week}', False):
        st.session_state[selection_key] = names[0]
    elif st.session_state.get(selection_key) not in names:
        st.session_state[selection_key] = names[0]
    col_snapshot, col_snapshot_info = st.columns([1.25, 2.75], gap='small', vertical_alignment='bottom')
    with col_snapshot:
        choice = st.selectbox('Snapshot originale Cboe', names, key=selection_key)
    path = f'{week}/snapshots/{choice}'
    try:
        snap = read_snapshot(store.download(path))
    except Exception as exc:
        st.error(f'Impossibile leggere lo snapshot: {type(exc).__name__}')
        return
    timestamp = str(snap.get('timestamp') or '')
    acquisition_label = _acquisition_label(store, week, logs, choice)
    download_clock = _snapshot_download_clock(store, week, logs, choice)
    # Confronto soltanto con il file precedente della STESSA giornata;
    # non interpreta il fuso del timestamp Cboe e non blocca il profilo.
    day_prefix = choice[:len('SPX_CBOE_') + 8]
    same_day_names = [name for name in names if name.startswith(day_prefix)]
    position = same_day_names.index(choice)
    preceding = same_day_names[position + 1] if position + 1 < len(same_day_names) else None
    if preceding is None:
        quality = timestamp_state(timestamp)
    else:
        try:
            previous_stamp = _snapshot_cboe_stamp(week, preceding, store)
            quality = timestamp_state(timestamp, previous_stamp, has_previous=True)
        except Exception:
            quality = "NON VERIFICABILE"
    quality_label = {
        "PRIMO RILEVAMENTO": "primo rilevamento",
        "DIVERSO": "timestamp diverso",
        "RIPETUTO": "timestamp ripetuto",
        "NON VERIFICABILE": "non verificabile",
    }[quality]
    with col_snapshot_info:
        st.caption(f'Rilevamento selezionato: {acquisition_label} · '
                   f'Timestamp Cboe: {timestamp or "n.d."} · '
                   f'Cboe: {quality_label} · file {choice}')
    # Si usa la data dello snapshot per gli esempi e per impedire look-ahead.
    try:
        day = date.fromisoformat(choice.split('SPX_CBOE_')[1][:8][:4] + '-' + choice.split('SPX_CBOE_')[1][4:6] + '-' + choice.split('SPX_CBOE_')[1][6:8])
    except Exception:
        st.error('Data dello snapshot non riconosciuta nel nome del file.')
        return
    previous = snap['data'].get('prev_day_close')
    @st.cache_data(ttl=3600, show_spinner=False)
    def _auto_basis(target_day):
        result = download_and_calculate(target_day)
        # Non memorizziamo frame grafici nel widget di selezione.
        result.pop('bars', None)
        return result

    st.markdown('**Differenziale ES − SPX (sessione precedente)**')
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

    recalc_error_key = f'basis_recalc_error_{day.isoformat()}'

    def _recalculate_basis():
        # Callback Streamlit: viene eseguita PRIMA della creazione dei widget
        # nel rerun. Evita la modifica di basis_key dopo st.text_input.
        try:
            with st.spinner('Acquisizione e confronto barre ES/SPX a 15 minuti...'):
                _apply_auto_basis()
            st.session_state.pop(recalc_error_key, None)
            st.session_state.pop(f'basis_auto_error_{day.isoformat()}', None)
        except Exception as exc:
            # Non sovrascrivere il valore inserito manualmente in caso di errore.
            st.session_state[recalc_error_key] = str(exc)[:250]

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
        st.button('Ricalcola ES − SPX', key=f'auto_basis_{day}',
                  on_click=_recalculate_basis, use_container_width=True)
    if st.session_state.get(recalc_error_key):
        st.warning('Ricalcolo non disponibile: ' +
                   st.session_state[recalc_error_key] +
                   '. Mantengo il valore inserito.')
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
        'snapshot': choice,
    }
    asia_c1 = None
    asia_p1 = None
    computed_profiles = {}
    candidate_0dte = None
    if day.weekday() == 4:
        st.info('Venerdì: il profilo 0DTE è quello di oggi, mentre il Weekly usa il venerdì SUCCESSIVO (conferma diretta di Serafini).')
    # Build both profiles before drawing so the same numeric range is available.
    profile_errors = {}
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        try:
            computed_profiles[title] = profile(snap, expiry, ref)
        except ValueError as exc:
            profile_errors[title] = str(exc)
    independent_scale = st.checkbox(
        'Ingrandisci ogni profilo con scala indipendente', value=False,
        key='gamma_independent_scale_v1',
        help='Disattivato: stessa scala per confrontare 0DTE e Weekly. '
             'Attivato: le lunghezze NON sono confrontabili tra i due profili.')
    shared_bounds = profile_gamma_bounds(computed_profiles.values())
    st.caption('Scale indipendenti: confrontare le intensità tramite i valori numerici.'
               if independent_scale else
               'Scala comune 0DTE / Weekly sul rilevamento selezionato: stesso valore = stessa lunghezza. '
               'La scala può cambiare tra rilevamenti.')
    st.caption('Tooltip: componente Call positiva, componente Put negativa e saldo netto '
               'della formula Serafini (OI + Volume) × Gamma; non valori monetari delle posizioni.')
    st.markdown('**Priorità operativa alle 0DTE:** Serafini richiama un peso numerico del '
                '**60% per le 0DTE** e del **23% per le Weekly** e, in caso di dubbio, '
                'privilegia i livelli 0DTE più marcati.')
    # Deviazioni Serafini cash M15, indipendenti dal riferimento SPX e dal
    # differenziale ES-SPX: prezzi di riferimento del future ES=F.
    # Un solo tentativo con cache; nessuna deviazione stimata se mancano M15.
    try:
        sigma_levels = serafini_cash_levels(day) if offset is not None else None
        sigma_error = None
    except Exception as exc:
        sigma_levels = None
        sigma_error = f'{type(exc).__name__}: {str(exc)[:115]}'
    profile_cols = st.columns(2, gap='small')
    for (title, expiry), profile_col in zip(
            [('0DTE', day), ('Weekly', weekly_exp)], profile_cols):
        with profile_col:
            if title in profile_errors:
                st.error(f'Impossibile costruire il profilo {title}: {profile_errors[title]}')
                continue
            result = computed_profiles[title]
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
                customdata=[gamma_components(row) for row in records],
                hovertemplate=(
                    'Strike %{y:,.2f}<br>NET GAMMA: %{x:,.3f}'
                    '<br>Componente Call (+): %{customdata[0]:,.3f}'
                    '<br>Componente Put (−): %{customdata[1]:,.3f}<extra></extra>'
                ),
            )
            fig.update_layout(
                height=max(520, len(y) * 15),
                margin=dict(l=15, r=20, t=40, b=30),
                title=f'{title} | {day:%d/%m/%Y} | {capture_info}<br><sup>NET GAMMA per strike — positivo a destra / negativo a sinistra</sup>',
                xaxis=dict(title='NET GAMMA (unità della formula Serafini)', zeroline=True,
                           range=(profile_gamma_bounds([result]) if independent_scale else shared_bounds),
                           fixedrange=not independent_scale,
                           zerolinewidth=1, zerolinecolor='#b7bec8'),
                yaxis=dict(title='Strike ES' if offset is not None else 'Strike SPX',
                           tickmode='array', tickvals=y,
                           ticktext=[f'{v:g}' for v in y], type='linear'),
                showlegend=False,
                template='plotly_dark',
            )
            fig.add_vline(x=0, line_width=1, line_color='#b7bec8')
            # Riferimento ES comune ai due istogrammi: Close SPX precedente
            # (stesso ref usato per selezionare gli strike) + basis ES-SPX.
            # Non alterare l'ordinamento, le barre o il NET GAMMA.
            if offset is not None and math.isfinite(offset):
                previous_close_es = float(ref) + offset
                fig.add_hline(
                    y=previous_close_es, line_dash='dash',
                    line_width=1.5, line_color='#f0cc65',
                )
                # Guide percentuali rispetto allo STESSO Close prec. ES.
                # Sono solamente riferimenti grafici; il NET GAMMA resta intatto.
                for pct in (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5):
                    target = previous_close_es * (1.0 + pct / 100.0)
                    emphasis = abs(pct) == 1.0
                    line_color = ('rgba(205,215,230,0.62)' if emphasis
                                  else 'rgba(195,205,220,0.46)')
                    fig.add_hline(
                        y=target, line_dash='dot',
                        line_width=1.8 if emphasis else 1.2,
                        line_color=line_color,
                    )
                    fig.add_annotation(
                        x=0.995, xref='paper', y=target, yref='y',
                        text=f'{pct:+.1f}%'.replace('.', ','),
                        showarrow=False, xanchor='right', yanchor='middle',
                        font=dict(
                            color=('rgba(225,232,240,0.92)' if emphasis
                                   else 'rgba(210,218,228,0.82)'),
                            size=10,
                        ),
                        bgcolor='rgba(13,17,23,0.78)', borderpad=1,
                    )
                # Il riferimento principale rimane visivamente prioritario.
                fig.add_annotation(
                    x=0.995, xref='paper', y=previous_close_es, yref='y',
                    text='Close prec.', showarrow=False,
                    xanchor='right', yanchor='bottom',
                    font=dict(color='#f0cc65', size=11),
                    bgcolor='rgba(13,17,23,0.85)', borderpad=2,
                )
            # SOLO 0DTE: 3 piccoli segmenti sul bordo sinistro, non estesi.
            if title == '0DTE' and sigma_levels is not None:
                _, clipped_sigma = add_sigma_markers(fig, sigma_levels, y)
            else:
                clipped_sigma = []
            st.plotly_chart(fig, use_container_width=True, key=f'gamma_net_{title}_{choice}')
            if title == '0DTE':
                if sigma_error:
                    st.caption('Deviazione Serafini M15 non disponibile: ' + sigma_error)
                elif offset is None:
                    st.caption('Deviazione Serafini ES M15: richiede differenziale ES-SPX per allineare il grafico.')
                elif sigma_levels and sigma_levels.get('status') != 'VALIDO':
                    st.caption('Deviazione Serafini M15 non disponibile: ' +
                               str(sigma_levels.get('reason', 'storico insufficiente')) +
                               '. Nessun livello ipotizzato.')
                elif sigma_levels and sigma_levels.get('status') == 'VALIDO':
                    st.caption('σ cash ES=F M15 (Chicago 08:30–15:00), rif. close precedente 15:15: '
                               f"L90 {sigma_levels['L90']:.2f} · "
                               f"L350 {sigma_levels['L350']:.2f} · "
                               f"S350 {sigma_levels['S350']:.2f}."
                               + (' Fuori dai 50 strike: ' + ', '.join(clipped_sigma) + '.'
                                  if clipped_sigma else '')
                               + ' Feed Yahoo: confrontare con Pine ES1! su TradingView.')

    # Dettagli numerici allineati sotto entrambi i grafici.
    detail_cols = st.columns(2, gap='small')
    for detail_title, detail_col in zip(('0DTE', 'Weekly'), detail_cols):
        with detail_col:
            detail_result = computed_profiles.get(detail_title)
            if detail_result and detail_result.get('rows'):
                detail_records = [
                    {**row, 'strike_es': row['strike_spx'] + offset if offset is not None else None}
                    for row in detail_result['rows']
                ]
                with st.expander(f'Dettaglio numerico {detail_title} (50 strike)', expanded=False):
                    import pandas as pd
                    st.dataframe(pd.DataFrame(detail_records), use_container_width=True, hide_index=True)
                st.download_button(
                    f'Scarica calcolo CSV {detail_title}',
                    profile_csv(detail_result, offset),
                    file_name=f'NET_GAMMA_{detail_title}_{day.isoformat()}_{choice}.csv',
                    mime='text/csv', key=f'csv_{detail_title}_{choice}',
                )
    if candidate_0dte is not None:
        with st.expander('STRUTTURA OPZIONI SPERIMENTALE', expanded=False):
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
                # Unica fonte dei livelli: selettori C1/P1 già esistenti.
                # Non visualizziamo ancora il quadro: verrà reso nel placeholder in alto.
                asia_c1 = (chosen['C1']['strike_spx'] + offset
                           if offset is not None and 'C1' in chosen else None)
                asia_p1 = (chosen['P1']['strike_spx'] + offset
                           if offset is not None and 'P1' in chosen else None)
                # Per il QUADRO delle 09:00 si possono usare soltanto livelli
                # acquisiti entro quell'ora; altrimenti sarebbero look-ahead.
                if download_clock is None or download_clock > '09:00':
                    asia_c1 = None
                    asia_p1 = None
                    st.caption('Quadro 09:00: livelli dello snapshot selezionato '
                               'successivi alle 09:00 o senza orario verificato. '
                               'Non li uso per ricostruire un verdetto storico.')
                # Espone i livelli effettivamente selezionati al report AI,
                # identificando snapshot e riferimento: mai riusare livelli di un altro giorno.
                st.session_state[f'asia_levels_{day.isoformat()}'] = {
                    'snapshot': choice, 'reference_spx': float(ref),
                    'basis': offset, 'c1_spx': chosen.get('C1', {}).get('strike_spx'),
                    'p1_spx': chosen.get('P1', {}).get('strike_spx'),
                    'selection': 'manuale su candidati sperimentali',
                }
                # Sezione separata: interpretazione della struttura, mai inferenza
                # automatica sul verso delle singole transazioni.
                with st.expander('STRUTTURA OPZIONI — 2 Vertical + Strangle', expanded=True):
                    # Identifica esattamente lo snapshot su cui sono basati
                    # livelli, struttura e payoff. Non inferire l'ora dal nome file.
                    selection_description = ('Ultimo rilevamento'
                                             if choice == names[0] else acquisition_label)
                    clock_label = download_clock or 'non disponibile'
                    st.caption(
                        f'Rilevamento: {selection_description} · {day:%d/%m/%Y} · '
                        f'download Roma {clock_label} · file {choice}'
                    )
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
                        st.caption('Il superamento dei livelli esterni richiede lettura del contesto e conferme; '
                                   'qui non si calcolano segnali, volatilità attesa o probabilità.')
            st.download_button('Scarica graduatoria barre NET GAMMA 0DTE (CSV)',
                               candidate_csv(candidate_0dte, offset),
                               file_name=f'Candidati_Top3_Gamma_{day.isoformat()}_{choice}.csv',
                               mime='text/csv', key=f'v9_candidates_{choice}')
    # Quadro storico delle 09:00 indipendente dallo snapshot selezionato.
    # Se il selezionato è successivo alle 09:00, recupera SOLTANTO il 07:30
    # ufficiale, verificandone il log con data/ora completa e scadenza 0DTE.
    asia_level_context = None
    if offset is not None and math.isfinite(offset):
        if asia_c1 is None or asia_p1 is None:
            log_names = tuple(sorted(str(item['name']) for item in logs))
            morning = _morning_asia_candidates(week, day, log_names, store)
            if morning:
                asia_c1 = morning['c1_spx'] + offset
                asia_p1 = morning['p1_spx'] + offset
                asia_level_context = (
                    'Riferimenti indicativi C1/P1 ricavati dallo snapshot UFFICIALE '
                    '07:30 Roma (' + morning['download_rome'][11:16] + ') · '
                    + morning['source']
                    + f" · SPX prev. {morning['reference_spx']:,.2f} "
                    + f"· basis ES-SPX {offset:+.0f} punti. "
                    'Metodo sperimentale top 3 per intensità: '
                    'NON sono C1/P1 confermati da Serafini.'
                )
        else:
            asia_level_context = (
                'C1/P1 selezionati manualmente nello snapshot corrente '
                'acquisito entro le 09:00 Roma. Metodo sperimentale; '
                'non costituisce conferma dei veri livelli Serafini.'
            )
    # Quadro dopo i profili e i selettori, sempre visibile fuori dall'expander.
    with st.expander('QUADRO OPERATIVO — ORE 09:00', expanded=False):
        # Il modulo originale di volatilita' e' visualizzato nel contesto iniziale.
        # Nessuna modifica a calcoli Gamma, C1/P1 o analisi asiatica.
        from asia_gamma import render_auto as render_asia_gamma
        render_asia_gamma(day, asia_c1, asia_p1, level_context=asia_level_context)
    st.subheader('Esporta i profili su TradingView — 4 situazioni')
    st.caption('Tutti e quattro i Pine usano una **scala NET GAMMA manuale**, '
               'senza adattamento automatico: se le barre si sovrappongono, '
               'aumenta tu i Gamma/barra. Nei Pine 2–4 le colonne sono '
               'più vicine (70 / 165 / 275 barre), con posizioni regolabili '
               'e possibilità di nascondere il precedente. '
               '**Usa un solo indicatore Serafini NET GAMMA alla volta:** '
               'più esportazioni simultanee possono sovrapporsi.')
    scripts = {}
    export_issues = {}
    if offset is None or not math.isfinite(offset):
        st.warning('Per esportare sul future ES inserisci un differenziale ES − SPX verificato.')
    else:
        try:
            sources = _pine_export_data(week, day, len(logs), store, logs)
            plans = (
                ('07:30', None),
                ('13:30', '07:30'),
                ('15:50', '07:30'),
                ('20:30', '15:50'),
            )
            for slot, preceding_slot in plans:
                current = sources.get(slot)
                preceding_frame = sources.get(preceding_slot) if preceding_slot else None
                if current is None:
                    export_issues[slot] = f'Rilevamento {slot} non disponibile o incompleto'
                    continue
                if preceding_slot and preceding_frame is None:
                    export_issues[slot] = f'Manca 0DTE {preceding_slot}, confronto non esportabile'
                    continue
                try:
                    current_p = current['profiles']
                    if preceding_slot:
                        scripts[slot] = make_pine_comparison(
                            preceding_frame['profiles']['0DTE'],
                            current_p['0DTE'], current_p['Weekly'], offset,
                            day.isoformat(), preceding_slot, slot,
                            current.get('cboe_timestamp') or '')
                    else:
                        # Esportatore 07:30 INVARIATO (V49), dati ufficiali 07:30.
                        scripts[slot] = make_pine(
                            current_p['0DTE'], current_p['Weekly'], offset,
                            day.isoformat(), current.get('cboe_timestamp') or '',
                            'Mattina 07:30')
                except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                    export_issues[slot] = str(exc)[:130]
        except Exception as exc:
            st.caption(f'Rilevamenti esportabili non leggibili: {type(exc).__name__}.')
            sources = {}
    columns = st.columns(4, gap='small')
    descriptions = (
        ('07:30', '07:30 · 0DTE + Weekly'),
        ('13:30', '07:30 0DTE → 13:30 0DTE + Weekly'),
        ('15:50', '07:30 0DTE → 15:50 0DTE + Weekly'),
        ('20:30', '15:50 0DTE → 20:30 0DTE + Weekly'),
    )
    for index, (slot, description) in enumerate(descriptions, 1):
        with columns[index - 1]:
            script = scripts.get(slot)
            st.download_button(
                f'{index} · Pine {slot}',
                data=script.encode('utf-8') if script else b'',
                file_name=f'Serafini_NET_GAMMA_{day:%Y-%m-%d}_{slot.replace(":", "")}.pine',
                mime='text/plain',
                key=f'pine_export_{week}_{day}_{slot}',
                use_container_width=True,
                disabled=script is None,
                help=description if script else export_issues.get(
                    slot, 'Necessario il differenziale ES-SPX'),
            )
            st.caption(description)
    if scripts:
        with st.expander('Mostra codice Pine da copiare e incollare'):
            selected_code = st.selectbox(
                'Situazione', list(scripts),
                format_func=lambda slot: f'{slot} — Pine pronto',
                key=f'pine_view_{week}_{day}')
            st.code(scripts[selected_code], language='pine')
    st.caption('TradingView → Pine Editor → Nuovo indicatore → incolla il codice '
               '→ Salva → Aggiungi al grafico ES. I quattro file usano soltanto '
               'i rilevamenti realmente disponibili; nessuna opzione live viene scaricata in TradingView.')
    st.caption('NET GAMMA(K) = (OI Call + VOL Call) × Gamma Call − (OI Put + VOL Put) × Gamma Put. Nessun fattore monetario aggiunto.')

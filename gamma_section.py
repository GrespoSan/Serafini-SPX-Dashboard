"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import gzip
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from net_gamma import friday_of, profile, profile_csv, read_snapshot


def render(store, week, snaps, logs):
    st.header('NET GAMMA · replica formula Serafini — verifica')
    st.warning('La formula è quella della slide 132. La selezione concreta dei 50 strike è ancora da verificare nel video: il criterio dei 50 più vicini è PROVVISORIO. Non usare i livelli per operare finché non è validato.')
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
    col1, col2 = st.columns(2)
    with col1:
        ref = st.number_input('Chiusura SPX precedente', min_value=0.01, value=float(previous or snap['data']['current_price']), step=0.25, format='%.4f', help='Riferimento per i 50 strike. Verificare che coincida con la chiusura cash precedente.')
    with col2:
        offset_txt = st.text_input('Differenziale ES − SPX (opzionale)', value='', placeholder='Es. 42.5', help='Lascia vuoto per il solo profilo SPX. Inserire un differenziale verificato del giorno precedente.')
    try:
        offset = None if not offset_txt.strip() else float(offset_txt.replace(',', '.'))
    except ValueError:
        st.error('Differenziale non numerico'); return
    weekly_exp = friday_of(day)
    if day.weekday() >= 5:
        st.error('Snapshot in giorno non operativo: scegliere manualmente una giornata valida.'); return
    st.caption(f'0DTE: {day.isoformat()} · Weekly (venerdì della settimana): {weekly_exp.isoformat()}. Selezione settimanale da verificare in caso di festività.')
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        result = profile(snap, expiry, ref)
        st.subheader(f'{title} · scadenza {expiry.isoformat()}')
        if not result['rows']:
            st.warning('Nessun contratto trovato per questa scadenza nello snapshot: nessun risultato inventato.')
            continue
        st.caption(f"{result['contracts']} contratti · {result['available_strikes']} strike disponibili · {len(result['rows'])} strike selezionati")
        records = [{**r, 'strike_es': r['strike_spx'] + offset if offset is not None else None} for r in result['rows']]
        import pandas as pd
        frame = pd.DataFrame(records)
        # Visualizzazione a due componenti: Call a destra e Put a sinistra.
        # I segni qui sono solo convenzione grafica: il calcolo NET GAMMA non cambia.
        import plotly.graph_objects as go
        y = frame['strike_es' if offset is not None else 'strike_spx'].tolist()
        calls = frame['call_gamma_component'].tolist()
        puts = frame['put_gamma_component'].tolist()
        fig = go.Figure()
        fig.add_bar(x=[-v for v in puts], y=y, orientation='h', name='PUT',
                    marker_color='#e45757', customdata=puts,
                    hovertemplate='Strike %{y:,.2f}<br>Put: %{customdata:,.2f}<extra></extra>')
        fig.add_bar(x=calls, y=y, orientation='h', name='CALL',
                    marker_color='#2dbb72',
                    hovertemplate='Strike %{y:,.2f}<br>Call: %{x:,.2f}<extra></extra>')
        max_val = max([abs(float(v)) for v in calls + puts] + [1.0])
        ticks = [-max_val, -max_val / 2, 0, max_val / 2, max_val]
        fig.update_layout(
            barmode='relative', height=max(520, len(y) * 15),
            margin=dict(l=15, r=20, t=40, b=30),
            title='CALL (verde) / PUT (rosso) per strike',
            xaxis=dict(range=[-max_val * 1.08, max_val * 1.08],
                       tickmode='array', tickvals=ticks,
                       ticktext=[f'{abs(v):,.0f}' for v in ticks],
                       title='Componente Gamma (valore assoluto)'),
            yaxis=dict(title='Strike ES' if offset is not None else 'Strike SPX',
                       tickmode='array', tickvals=y,
                       ticktext=[f'{v:g}' for v in y], type='linear'),
            legend=dict(orientation='h', y=1.04, x=0),
            template='plotly_dark',
        )
        fig.add_vline(x=0, line_width=1, line_color='#b7bec8')
        st.plotly_chart(fig, use_container_width=True, key=f'gamma_split_{title}_{choice}')
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(f'Scarica calcolo CSV {title}', profile_csv(result, offset),
                           file_name=f'NET_GAMMA_{title}_{day.isoformat()}_{choice}.csv', mime='text/csv', key=f'csv_{title}_{choice}')
    st.caption('NET GAMMA(K) = (OI Call + VOL Call) × Gamma Call − (OI Put + VOL Put) × Gamma Put. Nessun fattore monetario aggiunto.')

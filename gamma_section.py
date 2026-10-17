"""Modulo di sola lettura per l'app Serafini SPX Dashboard v2."""
import gzip
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import streamlit as st
from net_gamma import friday_of, profile, profile_csv, read_snapshot
from pine_export import make_pine


def render(store, week, snaps, logs):
    st.header('NET GAMMA · replica formula Serafini — verifica')
    st.info('Formula NET GAMMA della slide 132. Selezione dei livelli confermata da Serafini: 25 strike sopra e 25 sotto la chiusura SPX. La classificazione C1/P1 e il Gamma Flip non sono ancora automatizzati.')
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
    computed_profiles = {}
    if weekly_exp == day:
        st.info('Oggi è venerdì: 0DTE e Weekly scadono entrambe oggi. I due profili possono coincidere: la separazione grafica non crea due scadenze diverse.')
    for title, expiry in [('0DTE', day), ('Weekly', weekly_exp)]:
        try:
            result = profile(snap, expiry, ref)
        except ValueError as exc:
            st.error(f'Impossibile costruire il profilo {title}: {exc}')
            continue
        computed_profiles[title] = result
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
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(f'Scarica calcolo CSV {title}', profile_csv(result, offset),
                           file_name=f'NET_GAMMA_{title}_{day.isoformat()}_{choice}.csv', mime='text/csv', key=f'csv_{title}_{choice}')
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

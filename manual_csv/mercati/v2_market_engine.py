"""Multi-Market V2 SPX-adapted market engines.

Source: preserved independent validated NDX/GLD/USO engines of V1.
Called only within the cloned SPX-style ANALISI NET GAMMA dashboard.
No Supabase imports, persistent store access or scheduled downloads.
"""
import csv
import io
import math
from datetime import datetime, date
from zoneinfo import ZoneInfo
import streamlit as st

from manual_csv.mercati.markets import MARKETS
from manual_csv.mercati.gamma_chart_style import profile_chart, panel_heading, capture_display, spx_shared_gamma_bounds
from manual_csv.mercati.v2_visual_extension import expanded_display
from manual_csv.mercati.market_ui import render_empty_snapshot
from manual_csv.mercati.ndx_engine import (unpack_snapshot, available_expirations, close_from_payload,
                        profile, weekly_target, DEFAULT_BAND_PCT)
from manual_csv.mercati.ndx_basis import fetch_basis

ROME=ZoneInfo('Europe/Rome')


def render_selected_market(market_key):
    market=MARKETS[market_key]
    if market_key in ("RUSSELL", "DOW"):
        from manual_csv.mercati.v2_index_market_view import render as render_index_market
        render_index_market("RUT" if market_key == "RUSSELL" else "DJX")
        return
    if market_key == 'GOLD':
        from manual_csv.mercati.gld_cboe_view import render_gld_cboe
        render_gld_cboe()
        return
    if market_key == 'WTI':
        from manual_csv.mercati.uso_cboe_view import render_uso_cboe
        render_uso_cboe()
        return
    
    if not market.enabled:
        st.info(f'{market.label}: modulo predisposto, non ancora operativo. {market.info} '
                'Nessun livello sarà simulato senza una catena opzioni validata.')
        return
    
    
    blob=st.session_state.get('ndx_blob')
    if not blob:
        render_empty_snapshot('NDX')
        return
    try:
        doc=unpack_snapshot(blob)
        expiry_dates=available_expirations(doc)
    except Exception as exc:
        st.error(f'Fotografia non valida: {exc}')
        return
    if not expiry_dates:
        st.error('Nessuna scadenza NDX/NDXP riconosciuta: controllare il formato dei contratti.')
        return
    
    st.markdown(f'**File:** {st.session_state.get("ndx_name")} · '
                f'**Acquisizione:** {st.session_state.get("ndx_download_rome") or "non verificabile (file importato)"} · '
                f'**Timestamp Cboe originale:** `{doc.get("timestamp", "non dichiarato")}`')
    
    st.subheader('Riferimento indice e differenziale NQ − NDX')
    ref_auto=close_from_payload(doc)
    reference_input=st.text_input('Chiusura NDX della sessione precedente',
                                value=f'{ref_auto:.4f}' if ref_auto else '',
                                help='Dal campo prev_day_close della fotografia. Se assente, inserire valore verificato.')
    try:
        reference=float(reference_input.replace(',','.'))
        if reference<=0 or not math.isfinite(reference):raise ValueError()
    except ValueError:
        st.info('Inserisci una chiusura NDX precedente valida per procedere.')
        return
    
    st.session_state["v2_ndx_reference_current"] = reference
    now=datetime.now(ZoneInfo('America/New_York')).date()
    archive_ny_day = (
        st.session_state.get('v2_archive_capture_nyday_NDX')
        if st.session_state.get('v2_archive_choice_ndx') != 'manuale'
        else None)
    snapshot_day = archive_ny_day or now
    non_past=[d for d in expiry_dates if d >= snapshot_day]
    default_exp=(snapshot_day if snapshot_day in expiry_dates
                 else non_past[0] if non_past else expiry_dates[-1])
    day=st.selectbox('Scadenza 0DTE / giornata analizzata',expiry_dates,
                     index=expiry_dates.index(default_exp),format_func=lambda x:x.strftime('%d/%m/%Y'))
    if st.session_state.get('_ndx_basis_target') != snapshot_day.isoformat():
        st.session_state['_ndx_basis_target']=snapshot_day.isoformat()
        st.session_state['ndx_basis_value']=''
        st.session_state.pop('ndx_basis_manual_cleared', None)
        st.session_state.pop('ndx_basis_result',None)
        st.session_state.pop('ndx_basis_error',None)
        st.session_state.pop('ndx_basis_attempted',None)
    if day!=now:
        st.caption('0DTE storico / di studio: la scadenza selezionata non coincide '
                   'con la giornata corrente di New York.')
    elif archive_ny_day and day == archive_ny_day:
        st.caption('0DTE del rilevamento programmato selezionato.')
    weekly_default=weekly_target(day,expiry_dates)
    fridays=[x for x in expiry_dates if x.weekday()==4 and x>=day and x!=day]
    weekly_exp=st.selectbox('Scadenza Weekly (solo scadenze venerdì realmente presenti)',
                             [None]+fridays,
                             index=fridays.index(weekly_default)+1 if weekly_default in fridays else 0,
                             format_func=lambda x: 'Non disponibile' if x is None else x.strftime('%d/%m/%Y'))
    
    @st.cache_data(ttl=3600, show_spinner=False, max_entries=16)
    def _cached_basis(target_day):
        """Calcola il basis una volta per data e lo riusa nei rerun Streamlit."""
        try:
            massive_key=str(st.secrets.get("MASSIVE_API_KEY","")).strip()
        except Exception:
            massive_key=""
        return fetch_basis(target_day,massive_key)
    
    def _calculate_and_store_basis():
        try:
            result=_cached_basis(snapshot_day)
            st.session_state['ndx_basis_result']=result
            st.session_state['ndx_basis_value']=f"{result['basis']:.4f}"
            st.session_state.pop('ndx_basis_manual_cleared', None)
            st.session_state.pop('ndx_basis_error',None)
        except Exception as exc:
            st.session_state.pop('ndx_basis_result',None)
            st.session_state['ndx_basis_error']=str(exc)[:240]
    
    # Re-try once after the Massive date-bound correction, including sessions
    # that previously failed and kept the old ndx_basis_attempted flag.
    if st.session_state.get('ndx_basis_revision') != 'massive_15m_full_day_v2':
        st.session_state['ndx_basis_revision']='massive_15m_full_day_v2'
        st.session_state.pop('ndx_basis_attempted',None)
        st.session_state.pop('ndx_basis_error',None)
        if not st.session_state.get('ndx_basis_result'):
            st.session_state['ndx_basis_value']=''
    # Prima acquisizione del giorno: differenziale automatico; nessun clic richiesto.
    # Successivi rerun (download Pine, cambio widget) non sovrascrivono il manuale.
    if not st.session_state.get('ndx_basis_attempted'):
        st.session_state['ndx_basis_attempted']=True
        with st.spinner('Calcolo automatico NQ–NDX sulla sessione precedente...'):
            _calculate_and_store_basis()
    
    if st.button('Ricalcola differenziale NQ–NDX', help='Richiede un nuovo calcolo e aggiorna il valore modificabile'):
        _cached_basis.clear()
        with st.spinner('Aggiornamento differenziale NQ–NDX...'):
            _calculate_and_store_basis()
    if st.session_state.get('ndx_basis_error'):
        st.warning('Differenziale automatico temporaneamente non disponibile: '
                   + st.session_state['ndx_basis_error']
                   + '. Inserisci un valore verificato o usa Ricalcola.')
    
    # Streamlit can discard a keyed widget after a market/expander switch while
    # preserving the independent, successfully calculated basis result.
    # Rehydrate the editable field from that verified result before constructing
    # the widget; never default to an invented zero.
    if (st.session_state.get('ndx_basis_result')
            and not str(st.session_state.get('ndx_basis_value') or '').strip()
            and not st.session_state.get('ndx_basis_manual_cleared')):
        st.session_state['ndx_basis_value'] = (
            f"{st.session_state['ndx_basis_result']['basis']:.4f}")
    def _ndx_basis_edited():
        st.session_state['ndx_basis_manual_cleared'] = (
            not str(st.session_state.get('ndx_basis_value') or '').strip())
    basis_text=st.text_input('Differenziale NQ − NDX (modificabile)',key='ndx_basis_value',
                            on_change=_ndx_basis_edited,
                            help='Esporta livelli NQ soltanto con differenziale verificato. '
                                 'Contratto NQ trimestrale selezionato in automatico secondo il rollover CME. '
                                 'Calcolo della seduta cash precedente con Massive NQ e Yahoo NDX.')
    try:
        basis=float(basis_text.replace(',','.').strip()) if basis_text.strip() else None
        if basis is not None and not math.isfinite(basis):raise ValueError()
    except ValueError:
        st.error('Differenziale non numerico o non finito')
        return
    if st.session_state.get('ndx_basis_result'):
        res=st.session_state['ndx_basis_result']
        st.caption(f"Proposta automatica {res['basis']:+.2f} pt · "
                   f"**Contratto {res['tv_contract']}** ({res['ticker']}, fonte Massive) · "
                   f"seduta {res['reference_date']} · {res['bar_count']} barre comuni · "
                   f"min {res['minimum']:+.2f} / max {res['maximum']:+.2f} · "
                   f"rollover CME successivo {res['roll_day']:%d/%m/%Y}.")
    
    # Fascia comune 0DTE/Weekly: percentuale libera, centrata sul close NDX precedente.
    # Modificare la fascia cambia solo gli strike selezionati, mai la formula Gamma.
    band_pct=st.number_input('Fascia strike ± % dal Close precedente (0DTE e Weekly)',
                             min_value=0.1, max_value=10.0, value=3.0, step=0.1,
                             format='%.1f', key='ndx_strike_band_percent',
                             help='Unica ampiezza percentuale applicata alle due scadenze; '
                                  'include ogni strike reale dentro i limiti.')
    profiles={}
    for kind,exp in [('0DTE' if day == snapshot_day else 'Principale',day),('Weekly',weekly_exp)]:
        if exp is None:continue
        try:
            profiles[kind]=profile(doc,exp,reference,band_pct=band_pct)
        except Exception as exc:
            st.warning(f'{kind}: {exc}')
    if not profiles:
        st.error('Nessuno strike disponibile nella fascia percentuale: NET GAMMA e Pine non generati.')
        return
    show_extension = st.checkbox(
        "Estensione automatica NET GAMMA — solo grafici e Pine",
        value=True, key="v2_ndx_visual_extension",
        help="Estende soltanto la visualizzazione usando gli strike già nel JSON Cboe. "
             "Il nucleo e i calcoli originari NDX rimangono invariati.")
    visual_profiles = {}
    visual_notes = {}
    spot_ndx = doc["data"].get("current_price")
    for kind, group in profiles.items():
        if show_extension:
            visual_profiles[kind], visual_notes[kind] = expanded_display(
                group,
                lambda width, exp=date.fromisoformat(group["expiry"]): profile(
                    doc, exp, reference, band_pct=width),
                reference, spot_ndx, band_pct,
                strike_field="strike_index", gamma_field="net_gamma",
                upper_band_limit=20.0)
        else:
            visual_profiles[kind], visual_notes[kind] = group, {"extra": 0}
    
    # Stessa finestra di prezzo per 0DTE e Weekly, basata sul close indice precedente.
    band_low = reference * (1 - band_pct / 100)
    band_high = reference * (1 + band_pct / 100)
    # Identical 0DTE/Weekly X scale to SPX (raw values plus 8% span).
    shared_gamma_range = spx_shared_gamma_bounds(visual_profiles.values(), field='net_gamma')
    shared_ticks = sorted({float(r['strike_index']) + (basis or 0.0)
                           for p in visual_profiles.values() for r in p['rows']})
    if len(shared_ticks) > 80:
        shared_ticks = shared_ticks[::(len(shared_ticks) + 79) // 80]
    price_shift = basis if basis is not None else 0.0
    shown_strikes = [float(r["strike_index"])
                    for p in visual_profiles.values() for r in p["rows"]]
    shared_price_range = [min(band_low, min(shown_strikes)) + price_shift,
                          max(band_high, max(shown_strikes)) + price_shift]
    
    st.subheader(f'NET GAMMA — NDX → NQ · fascia ±{band_pct:g}%')
    from manual_csv.mercati.v2_gamma_summary import render_gamma_summary
    render_gamma_summary(visual_profiles.values(), net_key="net_gamma",
                         call_key="call_gamma", put_key="put_gamma")
    st.caption('Formula invariata: NET = [(OI+Volume)×Gamma] Call − [(OI+Volume)×Gamma] Put. '
               'Tutti gli strike Cboe disponibili tra '
               f'**{band_low:,.2f} e {band_high:,.2f} NDX** (±{band_pct:g}% dal close precedente). '
               'Stessa fascia prezzi e stessa scala Gamma per 0DTE e Weekly.')
    if basis is None:
        st.warning('Differenziale NQ–NDX assente: grafici in scala NQ non disponibili. '
                   'Non vengono mostrati strike NDX come se fossero NQ; '
                   'esportazione NQ disabilitata.')
    else:
        # Identico riferimento grafico SPX/ES: close indice precedente + basis future.
        # E' solo un livello guida e non interviene nel calcolo dei NET GAMMA.
        previous_close_nq = reference + basis
        st.caption(
            f'**Close NQ precedente (stimato)** = Chiusura NDX {reference:,.4f} '
            f'+ differenziale NQ–NDX {basis:+,.4f} = **{previous_close_nq:,.2f}**'
        )
    
    st.caption('Scala NET GAMMA comune a 0DTE e Weekly; confronto delle grandezze omogeneo.')
    cols=st.columns(len(profiles),gap='medium')
    for (kind,group),column in zip(profiles.items(),cols):
        with column:
            title, source_details = panel_heading(
                kind, group['expiry'], source_timestamp=doc.get('timestamp'),
                downloaded_rome=st.session_state.get('ndx_download_rome'),
                source=st.session_state.get('ndx_source', st.session_state.get('ndx_name', '')))
            st.subheader(title)
            st.caption(source_details)
            st.caption(f'{group["contract_count"]} contratti · '
                       f'{group["available_strikes"]} strike disponibili · '
                       f'{group["selected_strikes"]} originali · '
                       f'{visual_notes[kind].get("extra", 0)} aggiuntivi solo grafica/Pine')
            _, capture_label = capture_display(
                doc.get('timestamp'), st.session_state.get('ndx_download_rome'),
                st.session_state.get('ndx_source', st.session_state.get('ndx_name', '')))
            if visual_profiles[kind]['rows']:
                fg = profile_chart(
                    visual_profiles[kind]['rows'], 'NDX', reference,
                    shared_gamma_range,
                    shared_price_range, level_field='strike_index',
                    gamma_field='net_gamma', price_shift=price_shift,
                    axis_symbol='NQ' if basis is not None else 'NDX',
                    close_label='Close prec. proiettato' if basis is not None else 'Close prec. NDX',
                    ndx=True, show_close=True,
                    title=kind, snapshot_label=capture_label,
                    tick_levels=shared_ticks)
                st.plotly_chart(fg, use_container_width=True,
                                key=f'ndx_spx_gamma_{kind}_{group["expiry"]}')
    
    # Confronto Nasdaq con la stessa geometria e scala della versione SPX.
    # Candele NQ a sinistra; NET GAMMA a destra, senza ricalcolare i profili.
    from manual_csv.mercati.gamma_price_compare import render_optional_compare
    render_optional_compare(
        label='Nasdaq', key='ndx', yahoo_ticker='NQ=F',
        target_day=day, groups=profiles,
        strike_field='strike_index', gamma_field='net_gamma',
        source_name='NDX', price_name='NQ',
        price_scale=1.0, price_shift=basis if basis is not None else 0.0,
        reference_close=reference, snapshot_doc=doc,
        available=basis is not None,
        unavailable_reason='Per il confronto con le candele NQ '
                           'serve il differenziale NQ–NDX verificato.',
        toggle_key='ndx_show_candle_compare', radio_key='ndx_candle_profile',
    )

    # Pine NQ/MNQ: usa il generatore dedicato gia' esistente.
    # Traslazione solo se NQ-NDX e' disponibile, nessun basis zero inventato.
    if basis is not None:
        from manual_csv.mercati.pine_export import make_pine as make_ndx_pine
        from manual_csv.mercati.pine_ui import render_pine_controls
        original_count = sum(len(g['rows']) for g in profiles.values())
        extended_count = sum(len(g['rows']) for g in visual_profiles.values())
        # Il limite Pine e' 480 livelli; mai ridurre automaticamente gli strike.
        export_profiles = (visual_profiles if show_extension and extended_count <= 480
                           else profiles)
        if show_extension and extended_count > 480:
            st.caption(f'Pine NQ/MNQ: estensione {extended_count} strike oltre il limite '
                       f'di 480; esportati gli {original_count} strike originali '
                       'senza modifiche ai NET GAMMA.')
        try:
            main_kind = '0DTE' if '0DTE' in profiles else 'Principale'
            script = make_ndx_pine(
                export_profiles, basis, day, main_kind,
                acquisition_time=None,  # CSV Cboe: ora download non certificata
            )
            render_pine_controls(
                'NQ / MNQ', script, f'NDX_NQ_NET_GAMMA_{day.isoformat()}.pine',
                'ndx', source_ticker='NDX',
                price_factor=1.0, price_shift=basis,
            )
        except (ValueError, KeyError, TypeError) as exc:
            st.warning(f'Pine NQ/MNQ non generato: {exc}')
    else:
        st.caption('Pine NQ/MNQ non disponibile senza differenziale NQ–NDX verificato.')

"""Laboratorio unico C1-C3 / P1-P3, senza alterare la catena Cboe.

Un solo pannello di selezione, payoff e NET GAMMA; una sola tabella
tecnica nascosta di default. La simulazione non alimenta altre analisi.
"""
import math

import streamlit as st

from operational_levels import proposed_levels, candidate_csv
from option_structure import (
    build_structure, future_es_to_spx_strikes, make_study_levels,
)
from payoff_charts import render_payoff_charts

LABELS = ("C1", "C2", "C3", "P1", "P2", "P3")


def _valid_order(chosen):
    if any(key not in chosen for key in LABELS):
        return False
    try:
        s = {key: float(chosen[key]["strike_spx"]) for key in LABELS}
        return (len(set(s.values())) == len(LABELS)
                and s["C1"] < s["C2"] < s["C3"]
                and s["P1"] > s["P2"] > s["P3"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def _technical_table(rows):
    return [
        {
            "Livello": row["Livello"],
            "Future ES": row["Prezzo ES"],
            "Strike opzione SPX": row["Strike SPX"],
            "Ruolo ipotizzato": row["Ruolo ipotizzato"],
            "NET GAMMA Cboe": row["NET GAMMA"],
            "Lettura": row["Lettura operativa"],
        }
        for row in rows
    ]


def render_lab(candidate, original_profile, offset, day, choice, reference,
               snapshot_description, download_clock):
    """Mostra il laboratorio e restituisce SOLO i candidati Cboe originali.

    I sei strike manuali non tornano al chiamante e non diventano livelli
    operativi, segnalazioni o selezioni per il quadro delle 09:00.
    """
    suggestions = proposed_levels(candidate)
    prefix = f"option_lab_v2_{day.isoformat()}_{choice}_{reference}"
    with st.expander("STRUTTURA OPZIONI — Laboratorio · 2 Vertical + Strangle",
                     expanded=False):
        st.caption(
            f"{snapshot_description} · {day:%d/%m/%Y} · download Roma "
            f"{download_clock or 'N/D'} · "
            + (f"ES−SPX {offset:+.2f} punti"
               if offset is not None and math.isfinite(float(offset))
               else "Differenziale ES−SPX N/D")
        )
        st.caption(
            "Struttura ipotetica: il NET GAMMA aggregato non prova "
            "quali opzioni siano state comprate o vendute. "
            "I 50 strike Serafini e i dati Cboe restano invariati."
        )
        mode = st.radio(
            "Modalità di selezione",
            ("Candidati automatici", "Studio manuale ES"),
            horizontal=True, key=f"{prefix}_mode",
            help="Automatico: selezione fra strike Cboe effettivi. "
                 "Manuale: prezzi ES liberi per studiare il payoff, "
                 "senza cambiare gli altri moduli Serafini.",
        )
        chosen = {}
        proposal_copy = {key: dict(value) for key, value in suggestions.items()}
        stored_key = f"{prefix}_chosen_auto"
        if mode == "Candidati automatici":
            for side, group, heading in (
                ("call", ("C1", "C2", "C3"), "CALL · Vertical + Strangle"),
                ("put", ("P1", "P2", "P3"), "PUT · Vertical + Strangle"),
            ):
                entries = candidate[side]
                st.markdown(f"**{heading}**")
                if len(entries) < 3:
                    st.warning("Meno di tre candidati disponibili da Cboe.")
                options = [None] + [float(row["strike_spx"]) for row in entries]
                lookup = {float(row["strike_spx"]): row for row in entries}
                cols = st.columns(3, gap="small")
                for column, key in zip(cols, group):
                    default = proposal_copy.get(key, {}).get("strike_spx")
                    default_index = options.index(default) if default in options else 0
                    with column:
                        chosen_strike = st.selectbox(
                            f"{key} · future ES" if offset is not None else f"{key} · SPX",
                            options, index=default_index,
                            format_func=lambda value: (
                                "Non assegnato" if value is None else
                                (f"{value + offset:,.2f} ES"
                                 if offset is not None else f"{value:,.2f} SPX")),
                            key=f"{prefix}_auto_{key}",
                        )
                    if chosen_strike is not None:
                        chosen[key] = lookup[chosen_strike]
            if _valid_order(chosen):
                st.session_state[stored_key] = {
                    key: dict(chosen[key]) for key in LABELS
                }
        else:
            # Sempre separato dalla simulazione: per le analisi esterne
            # si usano gli ultimi candidati Cboe validi, o la proposta.
            chosen = {
                key: dict(value) for key, value in
                st.session_state.get(stored_key, proposal_copy).items()
            }
            if not _valid_order(chosen):
                chosen = proposal_copy

        study = chosen
        study_error = None
        if mode == "Studio manuale ES":
            if offset is None or not math.isfinite(float(offset)):
                study_error = (
                    "Per inserire i prezzi ES serve il differenziale ES−SPX "
                    "nel pannello ANALISI NET GAMMA."
                )
            elif not _valid_order(chosen):
                study_error = "Servono sei livelli Cboe ordinati come valori iniziali."
            else:
                es_prefix = f"{prefix}_es_{float(offset):+.6f}"
                if st.button("Ripristina livelli ES", key=f"{es_prefix}_reset"):
                    for key in LABELS:
                        st.session_state.pop(f"{es_prefix}_{key}", None)
                    st.rerun()
                prices = {}
                for group, title in (
                    (("C1", "C2", "C3"), "CALL · prezzi future ES"),
                    (("P1", "P2", "P3"), "PUT · prezzi future ES"),
                ):
                    st.markdown(f"**{title}**")
                    cols = st.columns(3, gap="small")
                    for col, key in zip(cols, group):
                        with col:
                            prices[key] = st.number_input(
                                f"{key} · prezzo ES", min_value=0.25,
                                value=float(chosen[key]["strike_spx"]) + float(offset),
                                step=0.25, format="%.2f",
                                key=f"{es_prefix}_{key}",
                            )
                try:
                    study = make_study_levels(
                        chosen, future_es_to_spx_strikes(prices, offset),
                        original_profile,
                    )
                except (ValueError, TypeError, OverflowError) as exc:
                    study_error = str(exc)
                if study_error is None:
                    st.caption(
                        "SIMULAZIONE: livelli manuali sul future ES, convertiti "
                        "internamente in strike SPX. Il NET GAMMA di uno "
                        "strike non presente in Cboe è N/D."
                    )
        if study_error:
            st.warning(study_error)
        elif not _valid_order(study):
            st.warning("Ordine non valido: C1 < C2 < C3 e P1 > P2 > P3. "
                       "I sei livelli devono essere distinti.")
        else:
            structure = build_structure(study, offset)
            if structure is not None:
                role_rows, measures = structure
                a, b, c = st.columns(3)
                a.metric("Vertical CALL", f"{measures['Vertical Call (punti)']:g} pt")
                b.metric("Vertical PUT", f"{measures['Vertical Put (punti)']:g} pt")
                c.metric("C3–P3 · distanza", f"{measures['Strangle esterno C3-P3 (punti)']:g} pt")
                render_payoff_charts(study, offset, original_profile)
                with st.expander("Dettagli tecnici · strike SPX, ruoli e NET GAMMA",
                                 expanded=False):
                    st.dataframe(_technical_table(role_rows),
                                 width="stretch", hide_index=True)
                    st.caption(
                        "C1/P1 acquistate; C2/P2 vendute nel modello didattico. "
                        "C3/P3: strangle short esterno. Il payoff è intrinseco "
                        "teorico a scadenza, senza premi o commissioni: NON è P&L."
                    )
                    st.download_button(
                        "Scarica graduatoria originale 0DTE (CSV)",
                        candidate_csv(candidate, offset),
                        file_name=f"Candidati_Top3_Gamma_{day.isoformat()}_{choice}.csv",
                        mime="text/csv", key=f"{prefix}_download",
                    )
        if mode == "Studio manuale ES":
            st.caption("Lo scenario manuale NON modifica i candidati Cboe "
                       "usati dalle altre analisi e dal quadro storico.")
    return chosen if _valid_order(chosen) else {}

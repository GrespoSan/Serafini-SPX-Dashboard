"""Presentation-only Serafini NET/CALL/PUT summaries for shown option profiles.

No Gamma calculations or original Cboe records are modified. The metrics
aggregate exactly the rows supplied by the rendered profile collection.
"""
import math
import streamlit as st


def format_gamma_it(value, *, signed=False):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Valore Gamma non finito")
    us = f"{value:+,.2f}" if signed else f"{value:,.2f}"
    return us.replace(",", "_").replace(".", ",").replace("_", ".")


def gamma_totals(profiles, *, net_key, call_key, put_key):
    rows = [row for p in profiles for row in p["rows"]]
    total = {
        "net": sum(float(row[net_key]) for row in rows),
        "call": sum(float(row[call_key]) for row in rows),
        "put": sum(float(row[put_key]) for row in rows),
    }
    if not all(math.isfinite(x) for x in total.values()):
        raise ValueError("Gamma non finito nelle scadenze visualizzate")
    # Keep the actual source net untouched; do not silently replace it.
    return total


def render_gamma_summary(profiles, *, net_key, call_key, put_key):
    totals = gamma_totals(profiles, net_key=net_key,
                          call_key=call_key, put_key=put_key)
    net_col, call_col, put_col = st.columns(3)
    net_col.metric("NET GAMMA · Call − Put",
                   format_gamma_it(totals["net"], signed=True))
    call_col.metric("GAMMA CALL (+)",
                    format_gamma_it(totals["call"], signed=True))
    put_col.metric("GAMMA PUT (−)",
                   "−" + format_gamma_it(totals["put"]))
    st.caption("Somma dei soli profili mostrati (scadenze e strike selezionati). "
               "Gamma originali CBOE, non monetari; nessuna esposizione dealer osservata.")
    return totals

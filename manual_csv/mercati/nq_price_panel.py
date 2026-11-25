"""Compatibilita' Nasdaq: unica base grafica SPX per tutti i mercati.

Il modulo Nasdaq principale richiama direttamente gamma_price_compare.
Queste funzioni conservano la firma precedente per eventuali altri chiamanti.
"""
from manual_csv.mercati.gamma_price_compare import load_closed_candles, comparison_chart


def load_nq_candles(day):
    """Solo candele NQ=F 5 minuti chiuse, con orario Roma."""
    return load_closed_candles("NQ=F", day)


def chart_compare_nq(profile, candles, basis, lower_ndx, upper_ndx, close_nq, kind):
    """Candele a sinistra, NET GAMMA a destra, prezzo sincronizzato."""
    return comparison_chart(
        profile, candles,
        strike_field="strike_index", gamma_field="net_gamma",
        source_name="NDX", future_name="NQ", group_name=kind,
        price_scale=1.0, price_shift=float(basis),
        reference_close=float(close_nq) - float(basis),
        zoom_complete=False,
    )

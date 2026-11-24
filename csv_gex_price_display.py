"""Solo presentazione: proiezione prezzi GEX SPX sull'asse ES/MES.

Il GEX rimane calcolato sullo SPX originale del CSV. In questa conversione
non si ricalcolano Gamma, esposizioni, scenario o regime: si trasla soltanto
l'asse dei prezzi con il medesimo differenziale usato dal Pine.
"""
import math


def gex_price_label(symbol: str) -> str:
    return "ES" if symbol == "SPX" else str(symbol)


def gex_display_price(value, symbol: str, es_spx_basis=None):
    """N/D (None) se non si conosce il differenziale per uno SPX.

    Per gli altri strumenti mantiene la scala di origine, senza
    introdurre falsi mapping tra ETF e futures.
    """
    if value is None:
        return None
    original = float(value)
    if not math.isfinite(original) or original <= 0:
        raise ValueError("Livello GEX non valido.")
    if symbol == "SPX":
        if es_spx_basis is None:
            return None
        shift = float(es_spx_basis)
        if not math.isfinite(shift):
            return None
        original += shift
    if not math.isfinite(original) or original <= 0:
        raise ValueError("Livello GEX equivalente non valido.")
    return original

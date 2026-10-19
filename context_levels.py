"""Contesto manuale e confronti numerici, NON segnali o attribuzioni automatiche."""


def suggest_buffer(change_pct=None, range_pct=None, vix_backwardation=None):
    """Indicazione prudente dalle risposte Serafini del 19/10/2026.

    In mancanza di contesto restituisce None, mai un valore implicito.
    Le soglie sono indicazioni qualitative, non un backtest.
    """
    if vix_backwardation is True:
        return 15, 'Backwardation VIX indicata manualmente: Serafini usa 15 punti.'
    if change_pct is None:
        return None, 'Inserisci almeno la variazione % della seduta precedente oppure seleziona il buffer manualmente.'
    if change_pct < -1.0:
        return 15, 'Seduta precedente in calo di oltre 1%: indicazione 15 punti.'
    if change_pct < 0 and range_pct is not None and range_pct > 1.0:
        return 15, 'Seduta precedente negativa con escursione >1%: indicazione 15 punti.'
    if -1.0 <= change_pct <= 1.0 and not (change_pct < 0 and range_pct is not None and range_pct > 1.0):
        if vix_backwardation is None:
            return None, '10 punti secondo variazione precedente, ma regime VIX non verificato: scegli manualmente o indica VIX.'
        return 10, 'Seduta precedente entro ±1% e VIX non in backwardation: indicazione 10 punti.'
    return None, 'Caso non definito con precisione dal docente (ad es. forte rialzo): seleziona il buffer manualmente.'

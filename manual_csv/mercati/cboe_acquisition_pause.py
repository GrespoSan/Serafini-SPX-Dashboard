"""Sospensione TEMPORANEA acquisizioni Cboe dal 20/11/2026.

Nessuna chiamata HTTP alla fonte Cboe finché l'utente non autorizza
esplicitamente il ripristino. Restano utilizzabili archivi, dati già
salvati, importazioni locali e Pine generati da file esistenti.

NON usare secrets, environment override o pulsanti per bypassare il blocco.
"""
CBOE_ACQUISITIONS_PAUSED = True
PAUSE_MESSAGE = (
    "Acquisizioni Cboe temporaneamente sospese dal 20/11/2026. "
    "Nessuna nuova richiesta ufficiale, Replay, di studio o manuale. "
    "Restano disponibili l'archivio esistente e l'importazione di file locali."
)


def require_cboe_acquisition_enabled():
    """Fail closed at every Cboe HTTP entrypoint."""
    if CBOE_ACQUISITIONS_PAUSED:
        raise RuntimeError(PAUSE_MESSAGE)

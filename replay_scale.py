"""Scala orizzontale adattiva dell'istogramma NET GAMMA nel Replay.

Intervallo simmetrico per frame: la linea verticale dello zero resta sempre
al centro del pannello (anche nella GIF e nella vista con candele ES).
La scala resta dinamica e NON permette di confrontare direttamente la
lunghezza assoluta delle barre tra rilevamenti differenti.
"""

import math


def dynamic_gamma_bounds(values):
    """Limiti simmetrici [-ampiezza, +ampiezza] con zero sempre al centro.

    Nessuna trasformazione ai dati NET GAMMA. Un margine dell'8% evita che
    la barra massima tocchi il bordo. Il minimo di 0.5 per lato garantisce
    una scala utilizzabile anche quando tutti i NET GAMMA sono zero.
    """
    numbers = [float(value) for value in values]
    if not numbers or not all(math.isfinite(value) for value in numbers):
        raise ValueError("Valori NET GAMMA mancanti o non finiti")
    amplitude = max(0.5, max(abs(value) for value in numbers)) * 1.08
    return [-amplitude, amplitude]

"""Scala orizzontale adattiva dell'istogramma NET GAMMA nel Replay.

La scala si calcola indipendentemente per ogni snapshot. Non modifica i
NET GAMMA e non e' adatta a confrontare visivamente ampiezze assolute tra frame.
"""

import math


def dynamic_gamma_bounds(values):
    """Intervallo [min, max] che include zero, con margini e ampiezza minima."""
    numbers = [float(value) for value in values]
    if not numbers or not all(math.isfinite(value) for value in numbers):
        raise ValueError("Valori NET GAMMA mancanti o non finiti")
    low = min(0.0, min(numbers))
    high = max(0.0, max(numbers))
    width = max(high - low, 1.0)
    margin = width * 0.08
    return [low - margin, high + margin]

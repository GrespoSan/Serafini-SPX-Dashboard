"""Controllo leggero della VARIAZIONE del timestamp Cboe, non della sua freschezza.

Il timestamp sorgente non dichiara un fuso orario verificabile:
si confronta esclusivamente il valore originale fra due acquisizioni.
Non si modificano dati, scadenze, NET GAMMA o Replay.
"""

_MISSING = frozenset(("", "none", "null", "n.d.", "n/a", "na", "-", "—"))


def _stamp(value):
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in _MISSING else text


def timestamp_state(current, previous=None, has_previous=False):
    """Stato prudente: niente supposizioni sul timezone o sulla data Cboe."""
    now = _stamp(current)
    if not now:
        return "NON VERIFICABILE"
    if not has_previous:
        return "PRIMO RILEVAMENTO"
    before = _stamp(previous)
    if not before:
        return "NON VERIFICABILE"
    return "RIPETUTO" if now == before else "DIVERSO"


def summarize_timeline(frames):
    """frames = [(slot, frame), ...] in ordine cronologico."""
    result = {"DIVERSO": 0, "RIPETUTO": 0, "NON VERIFICABILE": 0,
              "PRIMO RILEVAMENTO": 0}
    prior = None
    for index, (_, frame) in enumerate(frames):
        stamp = frame.get("cboe_timestamp_original")
        label = timestamp_state(stamp, prior, has_previous=index > 0)
        result[label] += 1
        prior = stamp
    return result

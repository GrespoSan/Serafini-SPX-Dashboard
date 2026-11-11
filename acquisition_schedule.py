"""Classificazione storica degli slot SPX - dati già archiviati immutabili.

Il 13:30 viene acquisito come STUDIO tramite replay.yml.
L'ultimo slot ufficiale 13:30 è datato 11/11/2026.
Dal 12/11/2026 il nuovo ufficiale è 14:46 Europe/Rome.

Questa policy NON genera download: serve ad interpretare i log REALI,
senza rinominare/retro-promuovere rilevamenti storici.
"""
from datetime import date

CUTOFF_1446 = date(2026, 11, 12)
FIRST_1330_OFFICIAL = date(2026, 11, 11)
LAST_1330_OFFICIAL = date(2026, 11, 11)

LEGACY_1330_CRON = "30 13 * * 1-5"
NEW_1446_CRON = "46 14 * * 1-5"

ALWAYS_OFFICIAL = {
    "07:30": "30 7 * * 1-5",
    "15:50": "50 15 * * 1-5",
    "17:00": "0 17 * * 1-5",
    "20:30": "30 20 * * 1-5",
}

ALL_SLOTS = (
    "07:30", "11:00", "13:30", "14:31", "14:46",
    "15:50", "16:01", "16:16", "16:31", "16:46",
    "17:00", "17:30", "18:00", "18:30", "20:00",
    "20:30", "21:30",
)


def official_slots_for_day(day):
    if not isinstance(day, date):
        raise TypeError("Data del rilievo obbligatoria")
    slots = dict(ALWAYS_OFFICIAL)
    if FIRST_1330_OFFICIAL <= day <= LAST_1330_OFFICIAL:
        slots["13:30"] = LEGACY_1330_CRON
    if day >= CUTOFF_1446:
        slots["14:46"] = NEW_1446_CRON
    return slots


def raw_study_1330_for_day(day):
    """Una catena storica alle 13:30 era STUDIO prima del 11/11."""
    return day < FIRST_1330_OFFICIAL


def expected_replay_slots(day):
    """14:46 non va marcato assente nei report anteriori al 12/11."""
    return tuple(x for x in ALL_SLOTS if
                 not (x == "14:46" and day < CUTOFF_1446))


def official_export_slots(day):
    base = ["07:30"]
    if FIRST_1330_OFFICIAL <= day <= LAST_1330_OFFICIAL:
        base.append("13:30")
    if day >= CUTOFF_1446:
        base.append("14:46")
    return tuple(base + ["15:50", "17:00", "20:30"])

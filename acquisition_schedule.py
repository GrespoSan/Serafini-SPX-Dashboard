"""Classificazione storica degli slot SPX - dati già archiviati immutabili.

Il 13:30 viene acquisito come STUDIO tramite replay.yml.
L'ultimo slot ufficiale 13:30 è datato 11/11/2026.
Dal 12/11/2026 il nuovo ufficiale e' 07:46 America/Chicago;
14:46 Roma di solito, 13:46 Roma durante il disallineamento DST.

Dal 16/11/2026 09:16 e 10:46 America/Chicago sostituiscono
08:50/10:00 Chicago (slot 16:16 e 17:46). Durante il disallineamento
sono le 15:16 e 16:46 Roma. Non si alterano le fotografie storiche.

Questa policy NON genera download: serve ad interpretare i log REALI,
senza rinominare/retro-promuovere rilevamenti storici.
"""
from datetime import date

CUTOFF_1446 = date(2026, 11, 12)
CUTOFF_1616_1746 = date(2026, 11, 16)  # prospettico: 09:16/10:46 Chicago
FIRST_1330_OFFICIAL = date(2026, 11, 11)
LAST_1330_OFFICIAL = date(2026, 11, 11)

LEGACY_1330_CRON = "30 13 * * 1-5"
NEW_1446_CRON = "46 14 * * 1-5"  # identificativo LOGICO, non orario in Roma
OFFICIAL_CHICAGO_TIME = "07:46"

ALWAYS_OFFICIAL = {
    "07:30": "30 7 * * 1-5",
    "15:50": "50 15 * * 1-5",
    "17:00": "0 17 * * 1-5",
    "20:30": "30 20 * * 1-5",
}

ALL_SLOTS = (
    "07:30", "11:00", "13:30", "14:31", "14:46",
    "15:50", "16:01", "16:16", "16:31", "16:46",
    "17:00", "17:30", "17:46", "18:00", "18:30", "20:00",
    "20:30", "21:30",
)


def official_slots_for_day(day):
    if not isinstance(day, date):
        raise TypeError("Data del rilievo obbligatoria")
    slots = dict(ALWAYS_OFFICIAL)
    if day >= CUTOFF_1616_1746:
        # These are logical slot IDs, anchored to Chicago (not fixed Rome).
        # The 15:50/17:00 log IDs remain HISTORICAL on earlier days.
        slots.pop("15:50")
        slots.pop("17:00")
        slots["16:16"] = "16 16 * * 1-5"
        slots["17:46"] = "46 17 * * 1-5"
    if FIRST_1330_OFFICIAL <= day <= LAST_1330_OFFICIAL:
        slots["13:30"] = LEGACY_1330_CRON
    if day >= CUTOFF_1446:
        slots["14:46"] = NEW_1446_CRON
    return slots


def raw_study_1330_for_day(day):
    """Una catena storica alle 13:30 era STUDIO prima del 11/11."""
    return day < FIRST_1330_OFFICIAL


def expected_replay_slots(day):
    """14:46 e' l'ID logico del 07:46 Chicago, attivo dal 12/11."""
    return tuple(x for x in ALL_SLOTS if
                 not (x == "14:46" and day < CUTOFF_1446)
                 and not (x == "17:46" and day < CUTOFF_1616_1746)
                 # 15:50 / 17:00 were official OLD slots only; the Replay
                 # downloader has never acquired those labels independently.
                 and not (x in ("15:50", "17:00") and
                          day >= CUTOFF_1616_1746))


def official_export_slots(day):
    """Cinque pulsanti Pine; 14:46 e' un ID logico ancorato a Chicago."""
    middle = "14:46" if day >= CUTOFF_1446 else "13:30"
    if day >= CUTOFF_1616_1746:
        return ("07:30", middle, "16:16", "17:46", "20:30")
    return ("07:30", middle, "15:50", "17:00", "20:30")

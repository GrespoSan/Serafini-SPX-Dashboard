"""Audit descrittivo e trasparente per l'export AI Serafini SPX.

Unifica le ALLERTE VOLUME senza confondere ufficiali e Replay.
Non interpreta automaticamente il fuso dei timestamp Cboe privi di offset.
Nessun effetto su NET GAMMA, quote, segni Gamma, buffer o altre metriche.
"""
import csv
import io
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def _aware(stamp):
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def snapshot_freshness(download_stamp, cboe_stamp, previous_cboe=None):
    """Eta' Cboe in minuti SOLO se entrambi gli orari dichiarano il fuso.

    Un timestamp Cboe senza fuso NON e' UTC implicito. Il momento del
    download e' sempre distinto dal momento a cui si riferisce la chain.
    """
    download = _aware(download_stamp)
    cboe_raw = str(cboe_stamp).strip() if cboe_stamp is not None else ""
    provider = _aware(cboe_raw)
    repeated = bool(previous_cboe and cboe_raw and
                    cboe_raw == str(previous_cboe).strip())
    info = {
        "download_rome": (download.astimezone(ROME).isoformat(timespec="seconds")
                          if download is not None else None),
        "cboe_timestamp_original": cboe_raw or None,
        "cboe_timezone_explicit": provider is not None,
        "cboe_timestamp_rome_verified": (
            provider.astimezone(ROME).isoformat(timespec="seconds")
            if provider is not None else None),
        "cboe_age_minutes": None,
        "cboe_timestamp_repeated_from_previous": repeated,
        "status": "NON DETERMINABILE",
        "note": "",
    }
    if download is None:
        info["status"] = "DOWNLOAD_SENZA_FUSO_VERIFICABILE"
        info["note"] = "Orario del download non verificabile; nessuna eta' stimata."
    elif not cboe_raw:
        info["status"] = "TIMESTAMP_CBOE_ASSENTE"
        info["note"] = "Cboe non riporta un timestamp verificabile."
    elif provider is None:
        info["status"] = "FUSO_CBOE_NON_DICHIARATO"
        info["note"] = ("Timestamp Cboe mantenuto letteralmente. Eta' N/D: "
                        "vietato presumere UTC, New York o Roma.")
    else:
        delta = (download - provider).total_seconds() / 60.0
        info["cboe_age_minutes"] = round(delta, 2)
        info["status"] = ("CBOE_SUCCESSIVO_AL_DOWNLOAD" if delta < -0.5
                          else "ETA_VERIFICABILE")
        info["note"] = (
            "Eta' calcolata da due timestamp con timezone esplicito; "
            "un ritardo non dimostra da solo una chain non valida."
        )
    return info


def attach_freshness_audit(report):
    """Aggiunge metadati temporali senza alterare timestamp o dati di Gamma."""
    official = sorted(report.get("snapshots", []),
                      key=lambda r: (r.get("download_utc") or "", r.get("file") or ""))
    previous = None
    for rec in official:
        rec["freshness"] = snapshot_freshness(
            rec.get("download_utc"), rec.get("cboe_timestamp_original"),
            previous,
        )
        previous = rec.get("cboe_timestamp_original") or None
    replay = report.get("intraday_replay") or {}
    timeline = replay.get("timeline") or []
    previous = None
    for item in timeline:
        item["freshness"] = snapshot_freshness(
            item.get("download_rome"), item.get("cboe_timestamp_original"),
            previous,
        )
        previous = item.get("cboe_timestamp_original") or None

    def counts(items):
        values = [x.get("freshness") or {} for x in items]
        return {
            "records": len(values),
            "age_verifiable": sum(v.get("cboe_age_minutes") is not None for v in values),
            "timezone_undeclared": sum(v.get("status") == "FUSO_CBOE_NON_DICHIARATO" for v in values),
            "repeated_timestamp": sum(bool(v.get("cboe_timestamp_repeated_from_previous"))
                                      for v in values),
            "download_missing_timezone": sum(v.get("status") == "DOWNLOAD_SENZA_FUSO_VERIFICABILE"
                                             for v in values),
        }
    report["freshness_audit"] = {
        "official": counts(official),
        "replay_timeline": counts(timeline),
        "method": ("Download UTC/Roma verificato separatamente dal timestamp originale Cboe. "
                   "Eta' disponibile solo per timestamp Cboe che includono un fuso "
                   "esplicito. Ripetizioni evidenziate; non sono prova di feed fermo."),
        "cboe_timezone_assumption": "NESSUNA",
        "futures_daily_source": (report.get("market_context", {})
                                 .get("previous_daily_range", {}).get("source")
                                 or "NON DISPONIBILE"),
    }
    return report["freshness_audit"]


def merge_volume_alerts(report):
    """Sintesi corretta: allerta ufficiali + Replay, mantenute separate.

    Gli stessi strike compaiono nel Replay e negli ufficiali; gli alert sono
    EVENTI tra due fotografie distinte, mai un conteggio di contratti.
    """
    current = report.get("volume_integrity") or {}
    official = []
    for entry in current.get("alerts", []):
        rec = dict(entry)
        rec["scope"] = "UFFICIALE"
        official.append(rec)
    replay = report.get("intraday_replay") or {}
    replay_alerts = []
    missing_comparison = 0
    for transition in replay.get("transitions") or []:
        for label, values in (transition.get("profiles") or {}).items():
            audit = (values or {}).get("volume_audit")
            if audit is None or audit.get("status") == "VOLUME/OI NON DISPONIBILI NEL COMPACT":
                missing_comparison += 1
                continue
            if audit.get("alert") is True:
                replay_alerts.append({
                    "scope": "REPLAY",
                    "initial_file": None, "final_file": None,
                    "from_slot": transition.get("from_slot"),
                    "to_slot": transition.get("to_slot"),
                    "profile": label, "audit": audit,
                })
    alert_count = len(official) + len(replay_alerts)
    total = {**current,
        "status": ("ALLERTA" if alert_count else
                   "NESSUNA ALLERTA NEI CONFRONTI VALUTABILI"),
        "alert_count": alert_count,
        "official_alert_count": len(official),
        "replay_alert_count": len(replay_alerts),
        "official_alerts": official,
        "replay_alerts": replay_alerts,
        "alerts": official + replay_alerts,
        "replay_comparisons_unavailable": missing_comparison,
        "replay_status": replay.get("status", "NON DISPONIBILE"),
        "scope_note": ("Conteggi separati per confronto: ufficiali vs Replay. "
                       "Non sono contratti né trade. Campi Volume/OI nulli "
                       "sono non confrontabili, mai trattati come zero."),
    }
    if replay.get("status") != "DISPONIBILE":
        total["scope_note"] += " Copertura Replay non disponibile; zero non significa assenza di problemi."
    report["volume_integrity"] = total
    if replay_alerts:
        report.setdefault("warnings", []).append(
            f"ALLERTA VOLUME REPLAY: {len(replay_alerts)} confronti non omogenei "
            "nella cronologia. Vedere volume_integrity.replay_alerts "
            "e il dettaglio PDF; non inferire posizioni comprate/vendute."
        )
    return total


def _csv_content(columns, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def volume_alerts_csv(report):
    rows = []
    for entry in (report.get("volume_integrity") or {}).get("alerts", []):
        audit = entry.get("audit") or {}
        rows.append({
            "ambito": entry.get("scope", "UFFICIALE"),
            "profilo": entry.get("profile"),
            "da_slot": entry.get("from_slot") or entry.get("initial_file"),
            "a_slot": entry.get("to_slot") or entry.get("final_file"),
            "stato": audit.get("status"),
            "volume_prima": audit.get("volume_initial"),
            "volume_dopo": audit.get("volume_final"),
            "variazione_volume_pct": audit.get("volume_change_pct"),
            "variazione_oi_pct": audit.get("oi_change_pct"),
            "interpretazione": "NON DIMOSTRA APERTURA/CHIUSURA POSIZIONI",
        })
    return _csv_content(
        ["ambito", "profilo", "da_slot", "a_slot", "stato",
         "volume_prima", "volume_dopo", "variazione_volume_pct",
         "variazione_oi_pct", "interpretazione"], rows)


def freshness_csv(report):
    rows = []
    for scope, records in (
        ("UFFICIALE", report.get("snapshots") or []),
        ("TIMELINE", (report.get("intraday_replay") or {}).get("timeline") or []),
    ):
        for rec in records:
            info = rec.get("freshness") or {}
            rows.append({
                "ambito": scope,
                "slot_o_file": rec.get("slot_rome") or rec.get("file"),
                "fonte": rec.get("source") or rec.get("acquisition_classification"),
                "download_roma": info.get("download_rome"),
                "timestamp_cboe_originale": info.get("cboe_timestamp_original"),
                "fuso_cboe_esplicito": info.get("cboe_timezone_explicit"),
                "eta_minuti_se_verificabile": info.get("cboe_age_minutes"),
                "timestamp_ripetuto": info.get("cboe_timestamp_repeated_from_previous"),
                "stato": info.get("status"),
                "nota": info.get("note"),
            })
    return _csv_content(
        ["ambito", "slot_o_file", "fonte", "download_roma",
         "timestamp_cboe_originale", "fuso_cboe_esplicito",
         "eta_minuti_se_verificabile", "timestamp_ripetuto", "stato", "nota"],
        rows)

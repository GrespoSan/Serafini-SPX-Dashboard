"""Lettura e validazione dei sidecar GEX EDGE Replay, senza calcoli Gamma.

Ogni confronto richiede lo stesso download originale: lo SHA-256 completo
è conservato nel compatto Serafini e nel report Edge indipendente.
"""
import hashlib
import json
import math
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
SLOT_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\.json$")
SCHEMA = "serafini_edge_replay_v1"


def _finite(value):
    if isinstance(value, bool):
        raise ValueError("Campo numerico non valido")
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Numero non finito")
    return n


def list_edge_slots(store, week, day):
    """Restituisce SOLO gli slot materialmente archiviati per la giornata."""
    available = []
    for item in store.list(f"replay-edge/{week}/"):
        name = str(item.get("name", ""))
        match = SLOT_FILE.fullmatch(name)
        if not match or match.group(1) != day.isoformat():
            continue
        hh, mm = int(match.group(2)), int(match.group(3))
        if hh > 23 or mm > 59:
            continue
        available.append((f"{hh:02d}:{mm:02d}", name))
    return sorted(set(available))


def validate_pair(compact, edge, day, slot):
    """Verifica sorgente, data, scadenza, download, metrica Edge e SHA.

    Non interpreta il timestamp Cboe grezzo come orario Roma.
    """
    if not isinstance(day, date) or not isinstance(compact, dict) or not isinstance(edge, dict):
        raise ValueError("Documenti Replay non validi")
    if compact.get("schema") != "serafini_replay_v1" or edge.get("schema") != SCHEMA:
        raise ValueError("Schema Replay/Edge incompatibile")
    if (compact.get("rome_date") != day.isoformat()
            or edge.get("rome_date") != day.isoformat()
            or compact.get("slot_rome") != slot or edge.get("slot_rome") != slot):
        raise ValueError("Giornata o slot non corrispondenti")
    if edge.get("source_kind") != "STUDIO_INTRADAY_NON_UFFICIALE":
        raise ValueError("Sidecar non proveniente dal Replay")
    a, b = compact.get("cboe_sha256"), edge.get("cboe_sha256")
    if (not isinstance(a, str) or not re.fullmatch("[0-9a-f]{64}", a)
            or a != b):
        raise ValueError("Impronta Cboe diversa fra Replay e Edge")
    if (compact.get("download_rome") != edge.get("download_rome")
            or compact.get("cboe_timestamp_original") != edge.get("cboe_timestamp_original")):
        raise ValueError("Download o timestamp Cboe non coincidenti")
    clock = datetime.fromisoformat(str(compact["download_rome"]).replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.astimezone(ROME).date() != day:
        raise ValueError("Download Replay senza orario Roma verificabile")
    if not math.isclose(_finite(compact["reference_spx"]),
                        _finite(edge["reference_spx"]), abs_tol=1e-8):
        raise ValueError("Chiusura riferimento SPX differente")
    if not math.isclose(_finite(compact["spot_spx"]),
                        _finite(edge["spot_spx"]), abs_tol=0.01):
        raise ValueError("Spot Cboe Serafini/Edge differente")
    if not compact.get("profiles", {}).get("0DTE", {}).get("rows"):
        raise ValueError("Profilo Serafini 0DTE assente")
    report = edge.get("edge")
    if not isinstance(report, dict) or report.get("schema") != "edge-gex-context-v1":
        raise ValueError("Report Edge non valido")
    if (report.get("symbol") != "SPX" or report.get("mode") != "0DTE"
            or report.get("expiries") != [day.isoformat()]
            or report.get("sha256") != a):
        raise ValueError("Catena, scadenza o SHA Edge non corrispondenti")
    if not math.isclose(_finite(report["spot_spx"]),
                        _finite(compact["spot_spx"]), abs_tol=0.01):
        raise ValueError("Spot report Edge differente")
    for key in ("net_gex", "call_gex", "put_gex", "gross_gex", "quality_pct"):
        _finite(report[key])
    if not math.isclose(_finite(report["call_gex"]) + _finite(report["put_gex"]),
                        _finite(report["net_gex"]), rel_tol=1e-9, abs_tol=0.01):
        raise ValueError("Componenti GEX Edge non riconciliate")
    if not isinstance(report.get("rows"), list) or not report["rows"]:
        raise ValueError("Profilo GEX Edge vuoto")
    status = "VALIDATO" if report.get("quality_sufficient") is True else "QUALITA_INSUFFICIENTE"
    if edge.get("status") != status:
        raise ValueError("Qualità GEX incongruente con lo stato archiviato")
    return report, clock.astimezone(ROME)


def read_pair(store, week, day, slot):
    filename = f"{day.isoformat()}_{slot.replace(':', '-')}.json"
    compact_bytes = store.download(f"replay/{week}/{filename}")
    edge_bytes = store.download(f"replay-edge/{week}/{filename}")
    if len(compact_bytes) > 250_000 or len(edge_bytes) > 2_000_000:
        raise ValueError("Report Replay troppo grande")
    compact = json.loads(compact_bytes)
    edge = json.loads(edge_bytes)
    report, clock = validate_pair(compact, edge, day, slot)
    return compact, edge, report, clock


def same_model(a, b):
    """Non confrontare GEX con scadenza o versione metodologica diversa."""
    e1, e2 = a["edge"], b["edge"]
    return (a.get("edge_engine_commit") == b.get("edge_engine_commit")
            and e1.get("model_version") == e2.get("model_version")
            and e1.get("expiries") == e2.get("expiries")
            and e1.get("mode") == e2.get("mode") == "0DTE"
            and e1.get("quality_sufficient") is True
            and e2.get("quality_sufficient") is True)

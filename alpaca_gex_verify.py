"""Strict Alpaca GEX/Serafini identity checks, pure Python, no Streamlit/Edge imports."""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ROME=ZoneInfo("Europe/Rome")
SOURCE="ALPACA_INDICATIVE_OCC_OI"
SCHEMA="serafini_alpaca_gex_pair_v1"
SNAP_NAME=re.compile(r"^SPX_ALPACA_\d{8}_\d{6}\.json\.gz$")
GEX_NAME=re.compile(r"^GEX_ALPACA_\d{8}_\d{6}_(official|replay)_[A-Za-z0-9_* -]+\.json$")

def _finite(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError,OverflowError):
        return None


def _aware(value):
    try:
        moment=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return moment if moment.tzinfo is not None else None
    except (ValueError,TypeError):
        return None


def _check_report(report,expiry):
    if not isinstance(report,dict):
        raise ValueError("GEX vuoto")
    if (report.get("schema")!="serafini_alpaca_gex_v1" or report.get("source")!=SOURCE
            or report.get("expiries")!=[expiry]):
        raise ValueError("Scadenza/provenienza GEX non corrispondente")
    for key in ("spot_spx","net_gex","gross_gex","call_gex","put_gex"):
        if _finite(report.get(key)) is None:
            raise ValueError(f"GEX numerico mancante: {key}")
    if not math.isclose(float(report["call_gex"])+float(report["put_gex"]),
                        float(report["net_gex"]),rel_tol=1e-8,abs_tol=.01):
        raise ValueError("Totale GEX diverso da Call+Put")
    if report.get("quality_sufficient") is not False:
        # The Alpaca indicative quality gate is not certified equivalent
        # to the upstream Edge v1.3 exposure/OI freshness gate.
        raise ValueError("Qualità GEX Alpaca non dichiarata diagnostica")
    rows=report.get("rows")
    curve=report.get("curve")
    if not isinstance(rows,list) or not 50<=len(rows)<=10000:
        raise ValueError("Profilo strike GEX insufficiente")
    if not isinstance(curve,list) or len(curve)!=201:
        raise ValueError("Curva scenario GEX non completa")
    last=None
    for point in curve:
        s=_finite(point.get("spot_spx")); y=_finite(point.get("net_gex"))
        if s is None or y is None or (last is not None and s<=last):
            raise ValueError("Curva GEX non ordinata/finita")
        last=s
    for row in rows:
        values=tuple(_finite(row.get(k)) for k in ("strike_spx","call_gex","put_gex","net_gex"))
        if any(v is None for v in values):
            raise ValueError("Valore strike GEX non numerico")
        if not math.isclose(values[1]+values[2],values[3],rel_tol=1e-8,abs_tol=.01):
            raise ValueError("GEX strike Call+Put incoerente")
    for key in ("call_wall_spx","put_wall_spx","gamma_flip_spx"):
        v=report.get(key)
        if v is not None and _finite(v) is None:
            raise ValueError("Livello GEX non valido")


def verify_pair(store, week, name):
    """Download and bind GEX pair to the exact official/Replay Serafini source."""
    if not isinstance(week,str) or not re.fullmatch(r"\d{4}-W\d{2}",week):
        raise ValueError("Settimana non valida")
    if not GEX_NAME.fullmatch(str(name)):
        raise ValueError("Nome del GEX Alpaca non ammesso")
    raw=store.download(f"gex-alpaca/{week}/{name}")
    if len(raw)>5_000_000:
        raise ValueError("Report GEX troppo grande")
    pair=json.loads(raw)
    if (not isinstance(pair,dict) or pair.get("schema")!=SCHEMA or
            pair.get("source")!=SOURCE or pair.get("symbol")!="SPX"):
        raise ValueError("Schema GEX Alpaca non verificato")
    stamp=_aware(pair.get("download_rome"))
    if stamp is None or pair.get("kind") not in ("official","replay"):
        raise ValueError("Data/download GEX mancante")
    digest=pair.get("source_snapshot_sha256")
    if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise ValueError("Hash sorgente GEX mancante")
    reports=pair.get("reports")
    if not isinstance(reports,dict) or set(reports)!= {"0DTE","Weekly"}:
        raise ValueError("Due scadenze GEX obbligatorie")
    for label,report in reports.items():
        dates=report.get("expiries") if isinstance(report,dict) else None
        if not isinstance(dates,list) or len(dates)!=1:
            raise ValueError("Scadenza GEX mancante")
        _check_report(report,dates[0])
        if report.get("source_snapshot_sha256")!=digest:
            raise ValueError("GEX generato da un'altra fotografia")
        if report.get("captured_utc")!=pair.get("captured_utc"):
            raise ValueError("Ora GEX incoerente")
    orig=pair.get("related_serafini_path")
    if not isinstance(orig,str):
        raise ValueError("Collegamento sorgente GEX mancante")
    if pair["kind"]=="official":
        expected=f"{week}/snapshots/{pair.get('source_snapshot_filename')}"
        if orig!=expected or not SNAP_NAME.fullmatch(str(pair.get("source_snapshot_filename"))):
            raise ValueError("File Serafini SPX/Alpaca non corrispondente")
        archived=store.download(orig)
        if hashlib.sha256(archived).hexdigest()!=digest:
            raise ValueError("Snapshot Serafini diverso dal GEX")
        snapshot=json.loads(gzip.decompress(archived))
        prov=snapshot.get("source_provenance") or {}
        if (prov.get("source")!=SOURCE or
                prov.get("kind")!="DERIVED_ALPACA_INDICATIVE_NOT_CBOE" or
                prov.get("download_utc")!=pair["captured_utc"]):
            raise ValueError("Provenienza della fotografia originale non verificata")
    else:
        if not re.fullmatch(rf"replay/{re.escape(week)}/\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}}\.json",orig):
            raise ValueError("Nome Replay sorgente non consentito")
        compact=json.loads(store.download(orig))
        meta=compact.get("source_provenance") or {}
        if (compact.get("source_snapshot_sha256")!=digest or
                compact.get("source")!=SOURCE or
                compact.get("kind")!="STUDIO_INTRADAY_ALPACA_DERIVED" or
                meta.get("kind")!="DERIVED_ALPACA_INDICATIVE_NOT_CBOE" or
                compact.get("download_rome")!=pair.get("download_rome")):
            raise ValueError("Replay e GEX non appartengono alla stessa acquisizione")
    return pair,stamp



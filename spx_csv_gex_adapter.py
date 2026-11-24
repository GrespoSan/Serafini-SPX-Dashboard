"""Source-specific bridge to the exact Edge GEX engine ported from Serafini CSV.

No network or OI re-download for archived Alpaca captures. The full original
chain is NOT present: the acquisition filters invalid options upstream.
Coverage on the retained sample is conditional and must not certify the
whole chain or authorize an operational export.
"""
from datetime import datetime, timezone
import math
from csv_gex_engine import _calendar, _report, MODEL_LABEL
from csv_gex_core import gex
from csv_gex_core.models import Contract, parse_occ
from spx_snapshot_source import visible_source
from volatility_levels import weekly_expiry

def analyze_alpaca_snapshot(doc, day):
    if visible_source(doc) != "ALPACA_INDICATIVE_DERIVED":
        raise ValueError("Provenienza Alpaca non verificata")
    meta = doc["source_provenance"]
    captured = datetime.fromisoformat(meta["source_quote_latest_utc"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("Timestamp Alpaca non valido")
    spot = float(doc["data"]["current_price"])
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("Spot SPX non valido")
    contracts = []
    seen = set()
    for row in doc["data"]["options"]:
        occ = parse_occ(row["option"])
        if occ.root not in ("SPX", "SPXW") or row["option"] in seen:
            raise ValueError("Contratto non SPX oppure duplicato")
        seen.add(row["option"])
        oi = row.get("open_interest")
        if oi is not None and (not math.isfinite(float(oi)) or oi < 0 or int(oi) != oi):
            raise ValueError("OI non valido")
        iv = row.get("iv")
        contracts.append(Contract(
            symbol=row["option"], root=occ.root, expiry=occ.expiry,
            settlement="AM" if occ.root == "SPX" else "PM",
            strike=occ.strike, type=occ.type, multiplier=100,
            oi=None if oi is None else int(oi),
            iv=float(iv) if iv is not None else None,
            oi_source=row.get("oi_source") or "NON_DOCUMENTATO",
            iv_source=row.get("gamma_source") or "ALPACA_INDICATIVE_MODELED",
        ))
    prepared = gex.finalize(contracts, spot, captured, _calendar(captured))
    reports = {}
    for label, expiry in (("0DTE", day), ("Weekly", weekly_expiry(day))):
        report = _report(expiry, label, prepared, spot, "SPX",
            "Copertura calcolata SOLO sui contratti conservati nello snapshot Alpaca; "
            "gli scarti a monte non sono ricostruibili. Copertura totale non certificata.")
        report["source"] = meta["source"]
        report["quality_certified"] = False
        report["coverage_scope"] = "RETAINED_CONTRACTS_ONLY"
        reports[label] = report
    return {"reports": reports, "spot": spot, "timestamp_utc": captured.isoformat(),
            "source": meta["source"], "model": MODEL_LABEL,
            "quality_certified": False,
            "expiry_quality": meta.get("expiry_quality", []),
            "download_utc": meta.get("download_utc"),
            "quote_age_mode": meta.get("quote_age_mode"),
            "oi_vintage": meta.get("oi_asof"),
            "oi_sources": sorted({c.oi_source for c in contracts}),
            "contracts": len(contracts)}

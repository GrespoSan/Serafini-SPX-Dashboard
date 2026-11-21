"""Immutable naming and attribution for SPX original vs independently derived chains.

No old Cboe file is renamed, modified or relabeled. Derived Alpaca
snapshots are not Cboe downloads and are never silently treated as such.
"""
from __future__ import annotations

import re
from datetime import date

PATTERN=re.compile(r"^SPX_(CBOE|ALPACA)_(\d{8})_(\d{6})(?:_[0-9a-f]{12})?\.json\.gz$")


def parse_name(name):
    m=PATTERN.fullmatch(str(name))
    if not m:
        raise ValueError("Nome snapshot SPX non riconosciuto")
    d=date.fromisoformat(f"{m[2][:4]}-{m[2][4:6]}-{m[2][6:8]}")
    return m[1],d,m[3]


def accepted(name):
    try:
        parse_name(name)
        return True
    except (ValueError,TypeError):
        return False


def source_label(name):
    kind,_,_=parse_name(name)
    return "Alpaca indicative · Gamma modellato" if kind=="ALPACA" else "Cboe originale / storico"


def date_label(name):
    _,day,_=parse_name(name)
    return day.strftime("%d/%m/%Y")


def same_source_day_prefix(name):
    kind,day,_=parse_name(name)
    return f"SPX_{kind}_{day:%Y%m%d}_"


def visible_source(snapshot):
    """Never accept an Alpaca snapshot lacking matching internal provenance."""
    if not isinstance(snapshot,dict):
        return "UNKNOWN"
    src=snapshot.get("source_provenance")
    if not isinstance(src,dict):
        return "CBOE_ORIGINAL_OR_MANUAL"
    if src.get("source")=="ALPACA_INDICATIVE_OCC_OI" and src.get("kind")=="DERIVED_ALPACA_INDICATIVE_NOT_CBOE":
        return "ALPACA_INDICATIVE_DERIVED"
    return "UNVERIFIED_PROVENANCE"

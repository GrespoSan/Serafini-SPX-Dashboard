"""Load the public upstream Edge engine at a pinned revision, without running it inside Serafini.
The separate, unmodified Edge adapter is edge_auto_engine.py. No filesystem/storage writes.
"""
import importlib
import io
import json
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path
import streamlit as st

UPSTREAM_COMMIT = "0f32354e63dba8650a6510219fa00eb5520f9b77"
UPSTREAM_URL = f"https://codeload.github.com/simonro/Edge-GEX-Lab/zip/{UPSTREAM_COMMIT}"
MAX_ZIP_BYTES = 12_000_000

@st.cache_resource(show_spinner="Caricamento motore GEX indipendente")
def _upstream():
    target = Path("/tmp/edge_gex_pinned_" + UPSTREAM_COMMIT[:12])
    dest = target / f"Edge-GEX-Lab-{UPSTREAM_COMMIT}"
    if not (dest / "gexlab" / "engine" / "gex.py").exists():
        request = urllib.request.Request(UPSTREAM_URL,headers={"User-Agent":"SPX-Edge-GEX-independent-study"})
        with urllib.request.urlopen(request,timeout=20) as response:
            payload = response.read(MAX_ZIP_BYTES+1)
        if len(payload)>MAX_ZIP_BYTES:
            raise ValueError("Archivio Edge troppo grande.")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.infolist():
                path=Path(member.filename)
                if path.is_absolute() or ".." in path.parts or not member.filename.startswith(f"Edge-GEX-Lab-{UPSTREAM_COMMIT}/"):
                    raise ValueError("Archivio Edge con percorso non consentito.")
                if member.is_dir():
                    continue
                if not member.filename.startswith(f"Edge-GEX-Lab-{UPSTREAM_COMMIT}/gexlab/"):
                    continue
                output=target/path
                output.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(member) as f:
                    output.write_bytes(f.read())
    if str(dest) not in sys.path:
        sys.path.insert(0,str(dest))
    return importlib.import_module("edge_auto_engine")

@st.cache_data(ttl=300, max_entries=12, show_spinner="Calcolo indipendente GEX dalla fotografia originale")
def result_from_snapshot(raw:bytes, offset:float):
    adapter=_upstream()
    result=adapter.analyze(raw,"0DTE")
    shift=float(offset)
    frame=result["rows"]
    def shifted(v):
        return float(v)+shift if v is not None else None
    return {
        "schema":"edge-gex-context-v1","source":"Edge GEX Lab upstream pinned",
        "symbol":result["symbol"],"mode":"0DTE",
        "timestamp":result["timestamp"],"valuation":result["valuation"],
        "sha256":hashlib.sha256(raw).hexdigest(),"expiries":result["expiries"],
        "spot_spx":result["spot"],"es_basis":shift,"spot_es":shifted(result["spot"]),
        "call_wall_spx":result["call_wall"],"put_wall_spx":result["put_wall"],
        "gamma_flip_spx":result["flip"],"call_wall_es":shifted(result["call_wall"]),
        "put_wall_es":shifted(result["put_wall"]),"gamma_flip_es":shifted(result["flip"]),
        "net_gex":result["net"],"gross_gex":result["gross"],"regime":result["regime"],
        "quality_pct":result["quality_pct"],
        "rows":[{"strike_spx":float(row["strike"]),"call_gex":float(row["call_gex"]),
                 "put_gex":float(row["put_gex"]),"net_gex":float(row["net_gex"])}
                for _,row in frame.iterrows()],
        "curve":[{"spot_spx":float(row["spot"]),"net_gex":float(row["net_gex"])}
                 for _,row in result["curve"].iterrows()],
    }

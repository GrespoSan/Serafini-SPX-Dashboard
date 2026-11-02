"""Solo lettura: snapshot SPX caricato localmente quando Supabase non risponde.

Nessun file caricato viene pubblicato, salvato nel repository o su Supabase.
Usa la medesima interfaccia list/download del modulo Storage per riutilizzare
il calcolo Serafini esistente senza cambiare formule o selezione degli strike.
"""
import gzip
import hashlib
import io
import json
import math
import re
from datetime import datetime
from zipfile import BadZipFile, ZipFile

MAX_COMPRESSED = 25 * 1024 * 1024
MAX_JSON = 90 * 1024 * 1024
RAW_PATTERN = re.compile(r"SPX_CBOE_\d{8}_\d{6}\.json\.gz")


class InvalidLocalSnapshot(ValueError):
    pass


def _read_gzip_bounded(payload):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as handle:
            raw = handle.read(MAX_JSON + 1)
    except (OSError, EOFError) as exc:
        raise InvalidLocalSnapshot("File gzip CBOE non valido.") from exc
    if len(raw) > MAX_JSON:
        raise InvalidLocalSnapshot("JSON decompresso troppo grande.")
    return raw


def load_local_snapshot(filename, payload):
    """Accetta ZIP GitHub Actions, .json.gz originale o JSON originale."""
    if len(payload) > MAX_COMPRESSED:
        raise InvalidLocalSnapshot("File superiore al limite di 25 MiB.")
    filename = str(filename).lower()
    if filename.endswith(".zip"):
        try:
            with ZipFile(io.BytesIO(payload)) as archive:
                valid = [x for x in archive.infolist()
                         if not x.is_dir() and RAW_PATTERN.fullmatch(x.filename)]
                if len(valid) != 1:
                    raise InvalidLocalSnapshot(
                        "Lo ZIP deve contenere una sola fotografia SPX_CBOE_*.json.gz.")
                if valid[0].file_size > MAX_COMPRESSED:
                    raise InvalidLocalSnapshot("Fotografia compressa troppo grande.")
                with archive.open(valid[0]) as handle:
                    payload = handle.read(MAX_COMPRESSED + 1)
        except BadZipFile as exc:
            raise InvalidLocalSnapshot("ZIP GitHub non valido.") from exc
        if len(payload) > MAX_COMPRESSED:
            raise InvalidLocalSnapshot("Fotografia compressa troppo grande.")
        raw = _read_gzip_bounded(payload)
    elif filename.endswith(".json.gz"):
        raw = _read_gzip_bounded(payload)
    elif filename.endswith(".json"):
        raw = payload
        payload = gzip.compress(raw, compresslevel=6, mtime=0)
    else:
        raise InvalidLocalSnapshot("Carica un file ZIP GitHub, .json.gz o .json CBOE.")

    try:
        document = json.loads(raw)
        if document.get("symbol") not in ("SPX", "_SPX"):
            raise ValueError("symbol")
        date_stamp = datetime.strptime(document["timestamp"], "%Y-%m-%d %H:%M:%S")
        options = document["data"]["options"]
        previous = float(document["data"]["prev_day_close"])
        spot = float(document["data"]["current_price"])
        if (not isinstance(options, list) or len(options) < 100
                or not all(math.isfinite(v) and v > 0 for v in (previous, spot))):
            raise ValueError("options/prezzo")
        if not any(math.isfinite(float(item.get("gamma") or 0))
                   and float(item.get("gamma") or 0) != 0
                   for item in options if isinstance(item, dict)):
            raise ValueError("gamma")
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError) as exc:
        raise InvalidLocalSnapshot(
            "Snapshot SPX non valido: servono timestamp, chiusura precedente, "
            "prezzo e opzioni con Gamma utilizzabile.") from exc

    sha = hashlib.sha256(raw).hexdigest()[:12]
    file_name = f"SPX_CBOE_{date_stamp:%Y%m%d_%H%M%S}_{sha}.json.gz"
    iso = date_stamp.date().isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    return LocalSnapshotStore(week, file_name, payload, document["timestamp"])


class LocalSnapshotStore:
    """Interfaccia read-only compatibile con Store, limitata a un solo file."""
    def __init__(self, week, filename, compressed, timestamp):
        self.week = week
        self.filename = filename
        self.compressed = compressed
        self.timestamp = timestamp
        self.object_path = f"{week}/snapshots/{filename}"

    def list(self, prefix):
        if prefix == f"{self.week}/snapshots/":
            return [{"name": self.filename, "id": "file-importato"}]
        return []

    def download(self, path):
        if path != self.object_path:
            raise FileNotFoundError("File non presente nell'importazione locale.")
        return self.compressed

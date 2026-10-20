"""Acquisizione SPX Cboe: primaria, recupero condizionale o manuale.

Non modifica il contenuto della catena scaricata. I recuperi non simulano orari passati.
"""
import gzip
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from storage import Store

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"
SCHEDULE_SLOTS = {
    "30 7 * * 1-5": ("mattina", "primario"),
    "45 7 * * 1-5": ("mattina", "recupero"),
    "30 15 * * 1-5": ("pomeriggio", "primario"),
    "45 15 * * 1-5": ("pomeriggio", "recupero"),
    "46 15 * * 1-5": ("pomeriggio", "primario"),
}
SUCCESS = {"NUOVO", "INVARIATO"}


def quality_report(options):
    """Conta i valori utilizzabili, senza modificare la catena Cboe.

    Un gamma numerico finito e diverso da zero è necessario per calcolare
    il NET GAMMA. Non imponiamo soglie percentuali arbitrarie: gli zeri
    possono essere normali per singoli contratti profondamente ITM/OTM.
    """
    import math

    fields = ("gamma", "delta", "iv", "volume")
    counts = {field: 0 for field in fields}
    for opt in options:
        for field in fields:
            try:
                value = float(opt.get(field) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value) and value != 0:
                counts[field] += 1
    return {"contracts": len(options), "nonzero": counts,
            "gamma_usable": counts["gamma"] > 0}


def validate_options(options):
    report = quality_report(options)
    if not report["gamma_usable"]:
        raise ValueError("Gamma assente o tutto a zero: NET GAMMA non calcolabile")
    return report


def get_slot(schedule):
    if not schedule:
        return "manuale", "manuale"
    if schedule not in SCHEDULE_SLOTS:
        raise ValueError(f"Evento pianificato non riconosciuto: {schedule!r}")
    return SCHEDULE_SLOTS[schedule]


def already_acquired(store, week, rome_date, period):
    """Un recupero si annulla solo se esiste un log di successo del primario.

    La data è quella di Roma; un successo manuale NON sostituisce il primario.
    """
    for entry in store.list(f"{week}/logs/"):
        try:
            raw = store.download(f"{week}/logs/{entry['name']}")
            item = json.loads(raw)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            continue
        if (item.get("rome_date") == rome_date
                and item.get("sessione") == period
                and item.get("tipo_tentativo") == "primario"
                and item.get("status") in SUCCESS):
            # Anche i vecchi log NUOVO potrebbero riferirsi a file
            # inutilizzabili, scaricati prima del controllo qualità.
            # In quel caso il recupero non deve essere bloccato.
            try:
                original = item.get("file")
                if not original:
                    continue
                payload = json.loads(gzip.decompress(store.download(original)))
                validate_options(payload["data"]["options"])
                return True
            except (KeyError, TypeError, ValueError, OSError, RuntimeError, EOFError):
                continue
    return False


class InvalidSnapshot(Exception):
    """Snapshot Cboe formalmente presente, ma non utilizzabile."""


def acquire(now=None, store=None, slot=None, fetch=None, sleep=None):
    now = now or datetime.now(timezone.utc)
    sleep = sleep or time.sleep
    rome = now.astimezone(ZoneInfo("Europe/Rome"))
    if rome.weekday() > 4:
        print("Weekend (nessun download)")
        return 0
    week = f"{rome.isocalendar().year}-W{rome.isocalendar().week:02d}"
    period, kind = get_slot(slot if slot is not None else os.environ.get("SLOT", ""))
    store = store or Store()
    meta = {
        "download_utc": now.isoformat(timespec="seconds"),
        "download_rome": rome.isoformat(timespec="seconds"),
        "scheduled_slot": os.environ.get("SLOT", "manuale") if slot is None else (slot or "manuale"),
        "sessione": period,
        "tipo_tentativo": kind,
        "rome_date": rome.date().isoformat(),
        "week": week,
        "status": "ERRORE",
    }
    try:
        if kind == "recupero" and already_acquired(store, week, meta["rome_date"], period):
            meta["status"] = "SALTATO_PRIMARIO_OK"
        else:
            # Un solo recupero a 60 secondi se la catena Cboe non contiene Gamma.
            # L'attesa avviene nel workflow GitHub, non in Streamlit.
            for attempt in (1, 2):
                if fetch is None:
                    req = Request(URL, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/json", "Cache-Control": "no-cache",
                    })
                    with urlopen(req, timeout=50) as response:
                        raw = response.read(90_000_001)
                else:
                    raw = fetch()
                if len(raw) > 90_000_000:
                    raise ValueError("Dimensione dati Cboe superiore a 90 MB")
                payload = json.loads(raw)
                stamp = payload.get("timestamp")
                opts = payload.get("data", {}).get("options")
                if not isinstance(stamp, str) or not isinstance(opts, list) or len(opts) < 100:
                    raise ValueError("JSON Cboe incompleto")
                report = quality_report(opts)
                meta.setdefault("tentativi_gamma", []).append({
                    "numero": attempt, "cboe_timestamp": stamp, "quality": report
                })
                meta["quality"] = report
                if report["gamma_usable"]:
                    break
                if attempt == 1:
                    print("Gamma tutti a zero: riprovo automaticamente tra 60 secondi", flush=True)
                    sleep(60)
            if not report["gamma_usable"]:
                meta["status"] = "DATI_NON_VALIDI"
                meta["error"] = ("Gamma assente o tutto a zero anche al secondo tentativo "+
                                 "dopo 60 secondi; file non archiviato")
                raise InvalidSnapshot(meta["error"])
            if len(meta["tentativi_gamma"]) == 2:
                meta["recupero_gamma_60s"] = True
            parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            filename = f"SPX_CBOE_{parsed:%Y%m%d_%H%M%S}.json.gz"
            path = f"{week}/snapshots/{filename}"
            meta.update(cboe_timestamp=stamp,
                        cboe_timestamp_timezone="non_specificato_nella_risposta",
                        spot_spx=payload["data"].get("current_price"),
                        contracts=len(opts))
            if filename in {x["name"] for x in store.list(f"{week}/snapshots/")}:
                meta.update(status="INVARIATO", file=path)
            else:
                buf = io.BytesIO()
                with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
                    gz.write(raw)
                data = buf.getvalue()
                store.upload(path, data, "application/gzip")
                meta.update(status="NUOVO", file=path,
                            sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    except InvalidSnapshot:
        # Lo stato esplicito DATI_NON_VALIDI è già impostato.
        pass
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    logkey = now.strftime("%Y%m%dT%H%M%S%fZ")
    try:
        store.upload(f"{week}/logs/{logkey}_{period}_{kind}.json",
                     json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
                     "application/json")
    except Exception as exc:
        print("ERRORE SALVATAGGIO LOG:", exc, file=sys.stderr)
        print(json.dumps(meta, ensure_ascii=False))
        return 1
    print(json.dumps(meta, ensure_ascii=False))
    return 0 if meta["status"] in SUCCESS | {"SALTATO_PRIMARIO_OK"} else 1


if __name__ == "__main__":
    raise SystemExit(acquire())

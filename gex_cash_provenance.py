"""Metadati cash SPX per la SOLA sezione GEX Edge.

Mostra l'ora dell'ultimo trade indice *se effettivamente presente* nel
payload Cboe. NON è l'ora del download, del modello o della quotazione ES.
Non attribuire un timezone a timestamp naive e non modificare il GEX.
"""
import gzip
import json
from datetime import datetime
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def extract_spx_trade_time(payload):
    """Solo data.last_trade_time dell'indice, mai una option.last_trade_time."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            raw = gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload
            payload = json.loads(raw)
        except (OSError, ValueError, UnicodeError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw = data.get("last_trade_time") or payload.get("last_trade_time")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > 64:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.year < 2000:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return raw


def describe_spx_trade(raw, download_clock=None):
    """Restituisce (descrizione, stato) senza inferire il fuso del provider."""
    if raw is None:
        return ("Ultimo trade indice SPX: N/D (non archiviato o non presente "
                "nella fotografia Cboe). Non usare l'ora del download come sostituto.",
                "non_verificabile")
    value = str(raw).strip()
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        stamp = when.astimezone(ROME) if when.tzinfo is not None else when
        label = stamp.strftime("%d/%m/%Y %H:%M:%S")
        clock_info = ("Roma, fuso convertito" if when.tzinfo is not None
                      else "fuso non dichiarato da Cboe")
        reference = ""
        status = "non_determinabile"
        if download_clock is not None:
            day = download_clock.astimezone(ROME).date()
            if stamp.date() < day:
                reference = " · data precedente al download"
                status = "seduta_precedente"
            elif stamp.date() > day:
                reference = " · data successiva al download: verificare fonte"
                status = "anomalia"
            else:
                reference = " · stesso giorno del download"
                status = "stesso_giorno"
        return (f"Ultimo trade indice SPX Cboe: {label} ({clock_info})"
                f"{reference}. È un timestamp del cash, non del future ES.",
                status)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return ("Ultimo trade indice SPX: N/D (timestamp Cboe non valido).",
                "non_verificabile")

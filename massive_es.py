"""Recupero STORICO facoltativo delle candele ES 5m con Massive Futures Basic.

Massive gratuito ha 8 ore di ritardo: NON sostituisce Yahoo nel quadro
operativo live delle 09:00. I dati sostitutivi sono usati solo quando:
  1) Yahoo presenta almeno una candela mancante;
  2) sono trascorse almeno 8h dalla fine del range asiatico;
  3) Massive fornisce TUTTE le 108 candele del medesimo contratto ES;
  4) almeno 20 barre sovrapposte Yahoo/Massive passano il controllo prezzi.
In caso contrario nessuna sostituzione e nessuna correzione artificiale.
"""
import os
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
API_BASE = "https://api.massive.com"
CONTRACT_PATTERN = re.compile(r"^ES[HMUZ][0-9]{1,2}$")


def _secret():
    try:
        value = st.secrets.get("MASSIVE_API_KEY")
    except (FileNotFoundError, KeyError, RuntimeError):
        value = None
    return str(value or os.environ.get("MASSIVE_API_KEY") or "").strip()


def _get(path, params, key):
    # Chiave solo nell'header HTTPS Authorization, mai nell'URL.
    # Non esposta nei log applicativi, nel JSON/PDF o nel repository.
    response = httpx.get(
        API_BASE + path, params=params,
        headers={"Authorization": "Bearer " + key},
        timeout=15, follow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Risposta Massive Futures senza elenco results")
    return data


def _candidate_contracts(target, key):
    data = _get("/futures/v1/contracts", {
        "product_code": "ES", "date": target.isoformat(),
        "active": "true", "type": "single", "limit": 300,
    }, key)
    candidates = []
    for row in data["results"]:
        ticker = str(row.get("ticker", ""))
        maturity = row.get("last_trade_date") or row.get("settlement_date")
        if not CONTRACT_PATTERN.fullmatch(ticker) or not maturity:
            continue
        try:
            last_trade = datetime.strptime(str(maturity)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if last_trade >= target:
            candidates.append((last_trade, ticker))
    return [ticker for _, ticker in sorted(set(candidates))[:2]]


def _download_session(target, ticker, key, expected):
    start = expected[0].to_pydatetime().astimezone(UTC)
    end = (expected[-1] + pd.Timedelta(minutes=5)).to_pydatetime().astimezone(UTC)
    ns_start = str(int(start.timestamp()) * 1_000_000_000)
    ns_end = str(int(end.timestamp()) * 1_000_000_000)
    data = _get("/futures/v1/aggs/" + ticker, {
        "resolution": "5min", "window_start.gte": ns_start,
        "window_start.lt": ns_end, "sort": "window_start.asc",
        "limit": 500,
    }, key)
    # Non unire pagine incomplete: 108 barre sono al di sotto del limite 500.
    if data.get("next_url"):
        raise ValueError("Aggregati Massive paginati: non utilizzare una serie parziale")
    output = []
    for bar in data["results"]:
        nanos = int(bar["window_start"])
        opening = pd.Timestamp(nanos, unit="ns", tz="UTC").tz_convert(ROME)
        values = {k: float(bar[k.lower()]) for k in ("Open", "High", "Low", "Close")}
        if not (values["Low"] <= min(values["Open"], values["Close"])
                and values["High"] >= max(values["Open"], values["Close"])
                and all(pd.notna(v) and v > 0 for v in values.values())):
            raise ValueError("OHLC Massive non coerenti")
        output.append((opening, values))
    if not output:
        return None
    df = pd.DataFrame(
        [row for _, row in output],
        index=pd.DatetimeIndex([t for t, _ in output]),
    ).sort_index()
    if df.index.has_duplicates:
        return None
    if len(df) != len(expected) or not df.index.equals(expected):
        return None
    return df


def _compare(yahoo, massive):
    common = yahoo.index.intersection(massive.index)
    if len(common) < 20:
        return None
    # Il contratto Yahoo ES=F e' continuo e puo' essere rollato prima
    # di quello Massive: la verifica impedisce di mescolare due contratti.
    y = yahoo.loc[common, ["Open", "High", "Low", "Close"]].astype(float)
    m = massive.loc[common, ["Open", "High", "Low", "Close"]].astype(float)
    diffs = (y - m).abs()
    close = diffs["Close"]
    per_bar_max = diffs.max(axis=1)
    median_close = float(close.median())
    p95_ohlc = float(per_bar_max.quantile(0.95))
    # Soglie caute, espresse in punti ES; NON dipendono dai valori Gamma.
    if median_close > 0.25 or p95_ohlc > 1.0:
        return None
    return {
        "compared_bars": len(common),
        "median_close_difference_points": round(median_close, 4),
        "p95_ohlc_difference_points": round(p95_ohlc, 4),
    }


def complete_historical_asia(target, yahoo_bars, expected):
    """Ritorna (sessione Massive completa, metadati) o (None, causa sintetica).

    yahoo_bars ha indice di APERTURA candela (Europe/Rome) e colonne OHLC.
    Sostituisce l'intera sessione soltanto dopo riscontro su barre comuni.
    """
    key = _secret()
    if not key:
        return None, {"status": "NON_CONFIGURATO", "reason": "MASSIVE_API_KEY non configurata"}
    end_utc = datetime.combine(target, time(9), tzinfo=ROME).astimezone(UTC)
    if datetime.now(UTC) < end_utc + timedelta(hours=8, minutes=5):
        return None, {"status": "DELAY_8H", "reason": "Massive Free: sessione non ancora interamente accessibile"}
    try:
        tickers = _candidate_contracts(target, key)
        if not tickers:
            return None, {"status": "NESSUN_CONTRATTO_ES"}
        for ticker in tickers:
            bars = _download_session(target, ticker, key, expected)
            if bars is None:
                continue
            check = _compare(yahoo_bars, bars)
            if check is None:
                continue
            original_missing = expected.difference(yahoo_bars.index)
            return bars, {
                "status": "INTEGRATO",
                "source": "Massive Futures " + ticker + " · 5min",
                "contract": ticker,
                "recovered_times": [t.strftime("%H:%M") for t in original_missing],
                **check,
                "method": "Sostituzione della sessione completa dopo confronto delle barre comuni",
                "limitations": "Dati Massive ritardati; non utilizzati come dato live alle 09:00",
            }
        return None, {"status": "VERIFICA_NON_SUPERATA",
                      "reason": "Contratto ES diverso da Yahoo oppure copertura Massive incompleta"}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        # Non esporre errori raw del provider o dettagli d'autenticazione.
        return None, {"status": "API_NON_DISPONIBILE",
                      "reason": "Impossibile verificare la sessione Massive con il piano/API configurati"}


def check_massive_connection(target):
    """Test esplicito della chiave Streamlit sul prodotto FUTURES + candle 5m.

    Effettua massimo 2 richieste nel piano Free. Nessun dettaglio
    dell'API key viene restituito o salvato nei dati di output.
    """
    key = _secret()
    if not key:
        return {"status": "CHIAVE_MANCANTE",
                "message": "MASSIVE_API_KEY non rilevata nei Secrets dell'app"}
    # La verifica usa un giorno gia' storico (non dipende dall'ora del test).
    test_day = target - timedelta(days=1)
    while test_day.weekday() >= 5:
        test_day -= timedelta(days=1)
    try:
        tickers = _candidate_contracts(test_day, key)
        if not tickers:
            return {"status": "NESSUN_CONTRATTO",
                    "message": "Autenticazione ricevuta; nessun ES disponibile per il test"}
        moment = pd.Timestamp(
            datetime.combine(test_day, time(5), tzinfo=ROME))
        for ticker in tickers:
            bars = _download_session(test_day, ticker, key,
                                     pd.DatetimeIndex([moment]))
            if bars is not None and len(bars) == 1:
                return {
                    "status": "OK",
                    "message": "Futures Massive: chiave, contratti ES e candele 5m accessibili",
                    "tested_day": test_day.isoformat(),
                    "contract": ticker,
                    "bar_rome": moment.strftime("%H:%M"),
                    "close_es": float(bars.iloc[0]["Close"]),
                    "time_delay": "Storico; non certifica disponibilita live alle 09:00",
                }
        return {"status": "SENZA_CANDELE",
                "message": "Chiave accettata, ma nessuna candela storica ES 5m restituita"}
    except httpx.HTTPStatusError as exc:
        # Non mostra URL, header o risposta raw potenzialmente sensibili.
        code = exc.response.status_code
        if code == 401:
            msg = "Autenticazione rifiutata: verifica la chiave Massive"
        elif code == 403:
            msg = "Accesso Futures negato: verifica il piano Futures Basic"
        elif code == 429:
            msg = "Limite richieste Massive raggiunto: riprova piu tardi"
        else:
            msg = "Errore HTTP Massive: " + str(code)
        return {"status": "HTTP_" + str(code), "message": msg}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        return {"status": "NON_DISPONIBILE",
                "message": "Test Massive fallito; controlla la connettivita o i log Cloud"}

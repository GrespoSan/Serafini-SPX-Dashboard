"""Private MT4 manual feed: same CSV, reference and basis; no automatic writes."""
import csv
import io
import math
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
from urllib.parse import quote
from urllib.request import Request, urlopen
from collections import defaultdict
from .net_gamma import profile, parse_contract
from .csv_gex_pine import validate_result, MODE_BY_LABEL
from volatility_levels import weekly_expiry

SOURCE="CBOE_CSV_MANUAL_USER_EXPORT"
PATH="mt4/manual100.csv"
ROME=ZoneInfo("Europe/Rome")
NY=ZoneInfo("America/New_York")

def rows100(doc,expiry,ref):
    core=profile(doc,expiry,ref)["rows"]
    by=defaultdict(lambda: [0.,0.,0,0])
    for option in doc["data"]["options"]:
        parsed=parse_contract(option["option"])
        if parsed is None or parsed[0]!=expiry:
            continue
        _,side,strike=parsed
        oi,vol,gamma=(float(option[k]) for k in ("open_interest","volume","gamma"))
        if not all(math.isfinite(x) and x>=0 for x in (oi,vol,gamma)):
            raise ValueError("Dati contratto non validi")
        pos=0 if side=="C" else 1
        by[strike][pos]+=(oi+vol)*gamma
        by[strike][2+pos]+=1
    below=sorted((s for s in by if s<ref),reverse=True)[:50]
    above=sorted(s for s in by if s>ref)[:50]
    chosen=sorted(below+above)
    if len(below)!=50 or len(above)!=50 or any(not all(by[s][2:]) for s in chosen):
        raise ValueError("MT4 richiede 50 strike reali per lato con Call e Put per ogni scadenza; carica CSV All.")
    result={s:by[s][0]-by[s][1] for s in chosen}
    if len(core)!=50 or any(not math.isclose(result[r["strike_spx"]],r["net_gamma"],abs_tol=1e-9) for r in core):
        raise ValueError("Nucleo 25+25 non coincidente")
    return sorted(result.items())

def build_feed(doc,ref,basis,basis_info,source,saved,iv_label,now=None):
    now=now or datetime.now(timezone.utc)
    stamp=datetime.strptime(doc["timestamp"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    day=stamp.astimezone(NY).date()
    rome=stamp.astimezone(ROME)
    prov=doc["_import_provenance"]
    sha=prov["original_csv_sha256"]
    if (prov["source"]!=SOURCE or prov.get("timestamp_utc_converted") is not True
            or prov.get("reference_market_day_ny")!=day.isoformat()
            or day!=now.astimezone(NY).date() or rome.date()!=now.astimezone(ROME).date()
            or day.weekday()>=5 or stamp>now):
        raise ValueError("MT4 accetta solo fotografie CSV della seduta corrente, non future.")
    if not math.isfinite(float(basis)) or not math.isfinite(float(ref)) or float(ref)<=0:
        raise ValueError("Differenziale o chiusura non validi")
    if not basis_info or not basis_info.get("reference_date"):
        raise ValueError("Prima verifica il differenziale ES-SPX della seduta precedente.")
    basis_day=date.fromisoformat(str(basis_info["reference_date"]))
    if basis_day>=day:
        raise ValueError("Data del differenziale non valida")
    basis=float(basis)
    meta=[("schema","serafini_mt4_v4"),("mode","Manuale"),("profile_strikes","100"),
          ("date_rome",rome.date().isoformat()),("slot_rome","manuale"),
          ("download_clock_rome",rome.strftime("%H:%M:%S")),("origin","CSV CBOE MANUALE"),
          ("basis_es_spx",f"{basis:.6f}"),("basis_date",basis_day.isoformat()),
          ("basis_source","Yahoo ES/SPX - valore applicato in Dashboard"),
          ("published_utc",now.isoformat()),("cboe_timestamp",doc["timestamp"]),
          ("snapshot_source",SOURCE),("snapshot_sha256",sha),
          ("source_timestamp_utc",stamp.isoformat()),("quote_age_mode","CSV_MANUAL")]
    # Same unextended 25+25 core per expiry as the dashboard; never sum the 100-strike display.
    core_profiles=[profile(doc,expiry,float(ref)) for expiry in (day,weekly_expiry(day))]
    if any(len(group["rows"])!=50 or len({r["strike_spx"] for r in group["rows"]})!=50
           for group in core_profiles):
        raise ValueError("NET GAMMA 25+25 incompleto")
    core_net=math.fsum(float(r["net_gamma"]) for group in core_profiles for r in group["rows"])
    if not math.isfinite(core_net):
        raise ValueError("NET GAMMA non finito")
    meta.extend([("serafini_net_gamma_core_25x2",f"{core_net:.12f}"),
                 ("serafini_net_gamma_core_strikes","100")])
    try:
        report,good=validate_result(saved,source,MODE_BY_LABEL.get(iv_label,"auto"))
        g=good["0DTE"]
        if (report["sha256"]!=sha or g["expiry"]!=day.isoformat()
                or datetime.fromisoformat(report["timestamp_utc"])!=stamp):
            raise ValueError("GEX fotografia diversa")
        if stamp.astimezone(NY).hour*60+stamp.astimezone(NY).minute < 570:
            if (report.get("spot_info") or {}).get("method")!="SPXW_0DTE_PUT_CALL_PARITY_R0_Q0":
                raise ValueError("GEX mattutino non ricalcolato con parità SPXW")
        sign={"positive":"Positive","negative":"Negative","neutral":"Zero"}[g["regime"]]
        net=float(g["net_gex"])
        if not math.isfinite(net): raise ValueError("GEX non valido")
        fields=[("gex_model","EDGE_0DTE"),("gex_source",SOURCE),
                ("gex_quality","DIAGNOSTICO_NON_CERTIFICATO"),
                ("gex_source_sha256",sha),("gex_source_timestamp_utc",stamp.isoformat()),
                ("gex_cboe_timestamp",doc["timestamp"]),("gex_expiry",day.isoformat()),
                ("gex_modeled_sign",sign),
                ("gex_net_usd_billion_per_1pct",f"{net/1e9:.12f}")]
        for key,label in (("call_wall","call_wall"),("put_wall","put_wall"),("flip","gamma_flip")):
            if g.get(key) is None: continue
            p=float(g[key])
            if math.isfinite(p) and p>0 and p+basis>0:
                fields.extend([(f"gex_{label}_spx",f"{p:.6f}"),(f"gex_{label}_es",f"{p+basis:.6f}")])
        meta+=fields
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError):
        pass # never fabricate GEX; NET GAMMA remains available
    buf=io.StringIO(newline="")
    writer=csv.writer(buf,lineterminator="\n")
    writer.writerows(meta)
    writer.writerow(["profile","expiry","strike_spx","price_es","net_gamma"])
    for label,expiry in (("0DTE",day),("Weekly",weekly_expiry(day))):
        for strike,net in rows100(doc,expiry,float(ref)):
            writer.writerow([label,expiry.isoformat(),f"{strike:.3f}",f"{strike+basis:.3f}",f"{net:.10f}"])
    return buf.getvalue().encode()

def publish(store,payload,role):
    if role!="admin": raise PermissionError("Solo amministratore")
    # A separate mutable object, not the immutable archive or automatic MT4 feed.
    endpoint=store.url+"/storage/v1/object/"+quote(store.bucket,safe="")+"/"+PATH
    req=Request(endpoint,data=payload,method="POST",headers={
        "apikey":store.key,"Authorization":"Bearer "+store.key,
        "Content-Type":"text/csv","x-upsert":"true","Cache-Control":"no-store"})
    with urlopen(req,timeout=25) as response:
        if not 200<=response.status<300: raise RuntimeError("Pubblicazione non riuscita")
    if store._download_uncached(PATH)!=payload:
        raise RuntimeError("Verifica del feed pubblicato non riuscita")

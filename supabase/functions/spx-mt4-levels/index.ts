Deno.serve(async (request: Request) => {
  const headers = { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" };
  if (request.method !== "GET") return new Response("Method not allowed", {status:405,headers});
  const expected = Deno.env.get("MT4_READ_TOKEN") ?? "";
  if (expected.length < 32) return new Response("Bridge not configured", {status:503,headers});
  const supplied = request.headers.get("x-mt4-token") ?? "";
  const digest = async (s:string) => new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s)));
  const a=await digest(expected),b=await digest(supplied);
  let diff=0; for(let i=0;i<a.length;i++)diff|=a[i]^b[i];
  if(diff!==0 || supplied.length!==expected.length)return new Response("Unauthorized",{status:401,headers});
  const base=Deno.env.get("SUPABASE_URL"),key=Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if(!base||!key)return new Response("Storage unavailable",{status:503,headers});
  try{
    // Replay rimane il valore predefinito e mantiene l'endpoint storico.
    // Ufficiale legge un CSV SEPARATO e non altera i dati Replay.
    const mode=new URL(request.url).searchParams.get("mode") ?? "replay";
    if(mode!=="replay" && mode!=="official" && mode!=="manual")
      return new Response("Invalid feed mode",{status:400,headers});
    const strikes=new URL(request.url).searchParams.get("strikes") ?? "50";
    if((strikes!=="50" && strikes!=="100") || (mode==="manual" && strikes!=="100"))
      return new Response("Invalid strike count",{status:400,headers});
    // MT4 v3 keeps its original 50-strike filenames; v4 is separate.
    const filename=mode==="manual" ? "manual100.csv" : mode==="official"
      ? (strikes==="100" ? "official100.csv" : "official.csv")
      : (strikes==="100" ? "latest100.csv" : "latest.csv");
    const source=await fetch(base+"/storage/v1/object/authenticated/spx-snapshots/mt4/"+filename,
      {headers:{"apikey":key,"Authorization":"Bearer "+key},signal:AbortSignal.timeout(12000)});
    if(!source.ok)return new Response(source.status===404?"Feed not ready":"Storage error",{status:source.status===404?404:502,headers});
    const body=await source.text();
    const schema=strikes==="100" ? "schema,serafini_mt4_v4\n" : "schema,serafini_mt4_v3\n";
    if(!body.startsWith(schema))return new Response("Invalid feed",{status:502,headers});
    if(mode==="manual"){
      const lines=body.split("\n");
      const meta=new Map(lines.filter(x=>x.split(",").length===2).map(x=>x.split(",") as [string,string]));
      const today=new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Rome",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
      const sourceTime=Date.parse(meta.get("source_timestamp_utc") ?? "");
      if(meta.get("mode")!=="Manuale" || meta.get("snapshot_source")!=="CBOE_CSV_MANUAL_USER_EXPORT" ||
         meta.get("date_rome")!==today || !Number.isFinite(sourceTime) || sourceTime>Date.now() ||
         !/^[a-f0-9]{64}$/.test(meta.get("snapshot_sha256")??""))
         return new Response("Manual feed not current or invalid",{status:404,headers});
    }
    return new Response(body,{headers:{...headers,"content-type":"text/csv; charset=utf-8"}});
  }catch(_){return new Response("Storage timeout",{status:504,headers});}
});
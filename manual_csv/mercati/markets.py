"""Market registry: Nasdaq NDX, Gold GLD and Oil USO enabled via Cboe."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Market:
    key: str
    label: str
    options: str
    futures: str
    basis: str
    enabled: bool
    info: str

MARKETS = {
    'NASDAQ': Market('NASDAQ','Nasdaq 100 (Cboe)','NDX / NDXP','NQ / MNQ','NQ − NDX',True,
                     'NDX index options: provisional transfer of the SPX 25+25 strike rule, pending cross-market validation.'),
    'RUSSELL': Market('RUSSELL','Russell 2000 (Cboe)','RUT / RUTW','RTY / M2K','RTY − RUT · M15 sincronizzate',True,
                       'Prezzo visuale RTY = strike RUT + basis verificato.'),
    'DOW': Market('DOW','Dow Jones (Cboe)','DJX / DJXW','YM / MYM','YM − DJIA · M15 sincronizzate',True,
                   'Prezzo visuale YM = strike DJX × 100 + basis verificato.'),
    'GOLD': Market('GOLD','Gold · GLD (Cboe)','GLD ETF options · Cboe','GC1! / MGC1! (continui)','GLD / GC=F · M5 sincronizzate',True,'Cboe GLD originale: non CME GC.'),
    'WTI': Market('WTI','Petrolio · USO (Cboe)','USO ETF options · Cboe','CL1! / MCL1! (continui)','USO / CL=F · M5 sincronizzate',True,'Cboe USO originale: non NYMEX CL.'),
}

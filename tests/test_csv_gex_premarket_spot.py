import unittest
from datetime import date
from csv_gex_engine import _premarket_spx_spot, analyze_csv_gex
import csv
import io

DAY=date(2026,11,25)

def options(spot=7111.6):
    out=[]
    for k in range(7030,7200,5):
        diff=spot-k
        for side,mid in (("C",30+max(diff,0)),("P",30+max(-diff,0))):
            out.append({"option":f"SPXW{DAY:%y%m%d}{side}{k*1000:08d}",
                        "bid":mid-.25,"ask":mid+.25})
    return out

def fixture(clock="10:47 AM GMT+1", bad=False):
    f=io.StringIO()
    w=csv.writer(f)
    w.writerow(["S&P 500 INDEX (^SPX)","Last: 7201.09","Change: -36.08"])
    w.writerow(["Date: November 25, 2026, "+clock])
    w.writerow(["Expiration Date","Calls","Last Sale","Net","Bid","Ask","Volume","IV","Delta","Gamma","Open Interest","Strike","Puts","Last Sale","Net","Bid","Ask","Volume","IV","Delta","Gamma","Open Interest"])
    for day in (DAY,date(2026,11,27)):
        for k in range(6800,7505,5):
            diff=7111.6-k
            c,p=30+max(diff,0),30+max(-diff,0)
            if bad: c=p=0
            w.writerow([day.strftime("%a %b %d %Y"),f"SPXW{day:%y%m%d}C{k*1000:08d}",c,0,max(0,c-.25),c+.25,10,.4,.5,.002,100,k,
                f"SPXW{day:%y%m%d}P{k*1000:08d}",p,0,max(0,p-.25),p+.25,20,.4,-.5,.002,300])
    return f.getvalue().encode()

class PremarketSpotTests(unittest.TestCase):
    def test_exact_parity_and_quality(self):
        spot,info=_premarket_spx_spot(options(),DAY)
        self.assertAlmostEqual(spot,7111.6)
        self.assertEqual(info["pairs"],34)
        self.assertFalse(info["certified"])
    def test_missing_crossed_and_incoherent_rejected(self):
        for chain in (options()[:8], [dict(x,bid=100,ask=1) for x in options()]):
            with self.assertRaisesRegex(ValueError,"SPX implicito N/D"):
                _premarket_spx_spot(chain,DAY)
        chain=options()
        for i in range(0,len(chain),4):
            chain[i]["bid"]+=20
            chain[i]["ask"]+=20
        with self.assertRaisesRegex(ValueError,"SPX implicito N/D"):
            _premarket_spx_spot(chain,DAY)
    def test_full_engine_and_no_fallback(self):
        result=analyze_csv_gex(fixture(),"SPX",iv_mode="decimal")
        self.assertAlmostEqual(result["spot"],7111.6)
        self.assertAlmostEqual(result["csv_last"],7201.09)
        self.assertEqual(result["spot_info"]["method"],"SPXW_0DTE_PUT_CALL_PARITY_R0_Q0")
        self.assertEqual(result["reports"]["0DTE"]["status"],"DIAGNOSTICO")
        self.assertEqual(result["reports"]["0DTE"]["regime"],"negative")
        with self.assertRaisesRegex(ValueError,"SPX implicito N/D"):
            analyze_csv_gex(fixture(bad=True),"SPX",iv_mode="decimal")
    def test_cash_session_preserves_original_spot(self):
        result=analyze_csv_gex(fixture("4:00 PM GMT+1",bad=True),"SPX",iv_mode="decimal")
        self.assertEqual(result["spot"],7201.09)
        self.assertEqual(result["spot_info"]["method"],"CSV_LAST")

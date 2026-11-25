"""Multi-market manual CSV parity: same source paths and safe independent feed."""
import csv
import io
import unittest
from datetime import date
from pathlib import Path
from streamlit.testing.v1 import AppTest

MARKETS = {"SP500":"SPX","Nasdaq":"NDX","Russell":"RUT","Dow Jones":"DJX",
           "Gold":"GLD","Petrolio":"USO","Azioni":None}
CASES = {"Nasdaq":("NDX","NDXP",25000,25,"NQ"),
         "Russell":("RUT","RUTW",2500,5,"RTY"),
         "Dow Jones":("DJX","DJX",450,1,"YM"),
         "Gold":("GLD","GLD",400,1,"GC"),
         "Petrolio":("USO","USO",80,.5,"CL"),
         "Azioni":("AAPL","AAPL",250,2.5,"AAPL")}

def fixture(ticker,root,spot,step):
    out=io.StringIO()
    w=csv.writer(out)
    w.writerow([f"TEST MARKET (^{ticker})",f"Last: {spot:.2f}","Change: 0.00"])
    w.writerow(["Date: November 23, 2026, 10:47 AM GMT+1"])
    w.writerow(["Expiration Date","Calls","Gamma","Volume","Open Interest","Strike",
                "Puts","Gamma","Volume","Open Interest"])
    for day in (date(2026,11,23),date(2026,11,27)):
        for i in range(-30,31):
            strike=spot+i*step
            k=round(strike*1000)
            w.writerow([day.strftime("%a %b %d %Y"),f"{root}{day:%y%m%d}C{k:08d}",
                        .002,10,100,strike,f"{root}{day:%y%m%d}P{k:08d}",.002,20,200])
    return out.getvalue().encode()

class MultiMarketTests(unittest.TestCase):
    def test_selector_exact_reference_and_no_upload(self):
        from manual_csv.csv_market import MARKETS as actual
        self.assertEqual(actual,MARKETS)
        at=AppTest.from_string("from manual_csv.panel import render\nrender()").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="manual_csv_mercato_selector").options,list(MARKETS))

    def test_all_manual_routes_render_profiles_and_pine(self):
        for market,(symbol,root,spot,step,future) in CASES.items():
            with self.subTest(market=market):
                payload=repr(fixture(symbol,root,spot,step))
                code=f"""
import io
from datetime import date
from unittest.mock import patch
import streamlit as st
from manual_csv.panel import render
st.session_state['manual_csv_mercato_selector']={market!r}
st.session_state['role']='admin'
st.session_state['manual_csv_gex_oi_source']='CSV'
st.session_state['gamma_visual_extension_v1']=True
class Upload(io.BytesIO):
    name='test.csv'
basis=dict(basis=16., reference_date=date(2026,11,20),bar_count=26,minimum=15.,maximum=17.,
           last=16.,source='test',tv_contract='CME:TESTZ2026',ticker='TESTZ6',roll_day=date(2026,12,10))
ratio=10. if {symbol!r}=='GLD' else 1.5
mapping=dict(etf_symbol={symbol!r},ratio=ratio,etf_price={spot!r},future_price=ratio*{spot!r},
             future_tv_symbol='COMEX:{future}1!',future_yahoo_ticker='{future}=F',bar_ny='2026-11-23 04:40')
with patch.object(st,'file_uploader',return_value=Upload({payload})), \\
     patch.object(st,'secrets',{{'MASSIVE_API_KEY':'test'}}), \\
     patch('csv_gex_services._cached_gex',return_value={{}}), \\
     patch('manual_csv.mercati.v2_market_engine.fetch_basis',return_value=basis), \\
     patch('manual_csv.mercati.v2_index_market_view._basis_cached',return_value=basis), \\
     patch('manual_csv.mercati.gld_cboe_view.load_chart_continuous_mapping',return_value=(mapping,None)), \\
     patch('manual_csv.mercati.uso_cboe_view.load_chart_continuous_mapping',return_value=(mapping,None)):
    render(mt4_store=object())
"""
                at=AppTest.from_string(code,default_timeout=30).run()
                self.assertEqual(len(at.exception),0,str(at.exception))
                self.assertEqual(len(at.error),0,str(at.error))
                self.assertGreaterEqual(len(at.get("plotly_chart")),1)
                self.assertGreaterEqual(len(at.get("download_button")),1)
                self.assertFalse(any(x.label=="INVIA A MT4" for x in at.button))
                self.assertEqual(at.session_state["manual_csv_gex_current_source"]["symbol"],symbol)
                self.assertTrue(at.session_state["gamma_visual_extension_v1"])

    def test_native_and_projected_axes(self):
        from manual_csv.mercati.v2_index_basis import CONFIG
        from manual_csv.mercati.etf_gamma_axes import chart_price_axes
        self.assertEqual(CONFIG["RUT"]["factor"],1.)
        self.assertEqual(CONFIG["DJX"]["factor"],100.)
        for symbol,future in (("GLD","GC"),("USO","CL")):
            native=chart_price_axes(symbol,None,[90,110],[100])
            self.assertEqual(native["axis_symbol"],symbol)
            projected=chart_price_axes(symbol,dict(etf_symbol=symbol,ratio=10.,etf_price=100.,
                future_price=1000.,future_tv_symbol=f"CME:{future}1!"),[90,110],[100])
            self.assertEqual(projected["axis_symbol"],future)
            self.assertEqual(projected["tick_levels"],[1000.])

    def test_copied_market_modules_no_automatic_namespace(self):
        for path in Path("manual_csv/mercati").glob("*.py"):
            self.assertNotIn("from mercati.",path.read_text(),path.name)
        text=Path("manual_csv/mercati/pine_ui.py").read_text()
        self.assertIn("manual_csv_gex_current_source",text)
        self.assertIn("manual_csv_gex_last_report",text)

if __name__=="__main__":
    unittest.main()

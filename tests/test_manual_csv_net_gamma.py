"""Manual CSV integration: separate state, source and exports; no network."""
import csv
import io
import unittest
from datetime import date
from pathlib import Path
from streamlit.testing.v1 import AppTest

def csv_fixture():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["S&P 500 INDEX (^SPX)", "Last: 6000.00", "Change: 0.00"])
    writer.writerow(["Date: November 23, 2026, 10:47 AM GMT+1"])
    writer.writerow(["Expiration Date","Calls","Gamma","Volume","Open Interest","Strike","Puts","Gamma","Volume","Open Interest"])
    for day in (date(2026,11,23),date(2026,11,27)):
        for strike in range(5600,6401,5):
            writer.writerow([day.strftime("%a %b %d %Y"),f"SPXW{day:%y%m%d}C{strike*1000:08d}",
                "0.002","10","100",str(strike),f"SPXW{day:%y%m%d}P{strike*1000:08d}","0.002","20","200"])
    return out.getvalue().encode()

class ManualCSVTests(unittest.TestCase):
    def test_read_only_and_canonical_values(self):
        from manual_csv.offline_snapshot import load_local_snapshot
        from manual_csv.net_gamma import profile, read_snapshot, visual_profile, gamma_component_totals
        from manual_csv.pine_export import make_pine
        store=load_local_snapshot("options.csv",csv_fixture())
        self.assertFalse(hasattr(store,"upload"))
        self.assertEqual(store.list(f"{store.week}/logs/"),[])
        snap=read_snapshot(store.download(store.object_path))
        self.assertEqual(snap["timestamp"],"2026-11-23 09:47:00")
        profiles=[]
        for expiry in (date(2026,11,23),date(2026,11,27)):
            p=profile(snap,expiry,6000)
            self.assertEqual(len(p["rows"]),50)
            vis=visual_profile(snap,p,expiry,6000,6000,buffer_pct=.05)
            self.assertGreater(len(vis["rows"]),50)
            self.assertLessEqual(len(vis["rows"]),200)
            profiles.append(dict(p,display_rows=vis["rows"]))
        totals=gamma_component_totals(profiles,require_core=True)
        self.assertAlmostEqual(totals["net"],-22.)
        pine=make_pine(*profiles,16,"2026-11-23",snap["timestamp"],current_only=True)
        self.assertIn("//@version=6",pine)
        self.assertNotIn("ALPACA",pine)
    def test_wrong_market_rejected(self):
        from manual_csv.offline_snapshot import load_local_snapshot, InvalidLocalSnapshot
        with self.assertRaises(InvalidLocalSnapshot):
            load_local_snapshot("wrong.csv",csv_fixture().replace(b"(^SPX)",b"(NDX)"))
    def test_app_without_upload_has_both_controls(self):
        at=AppTest.from_string("""
import streamlit as st
from manual_csv.panel import render
st.checkbox('Automatic',value=True,key='gamma_visual_extension_v1')
render()
""").run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.expander[0].label,"ANALISI NET GAMMA — CSV MANUALE")
        self.assertTrue(at.session_state["gamma_visual_extension_v1"])
    def test_manual_upload_ui_and_separate_state(self):
        data=repr(csv_fixture())
        code="""
import io
import streamlit as st
from unittest.mock import patch
from manual_csv.panel import render
st.checkbox('Automatic',value=True,key='gamma_visual_extension_v1')
st.text_input('Automatic basis',value='99',key='basis_override_2026-11-23')
st.number_input('Chiusura SPX precedente',value=6000.0, min_value=0.01, step=0.25, format='%.4f')
class Upload(io.BytesIO):
    name='options.csv'
with patch.object(st,'file_uploader',return_value=Upload("""+data+""")), \\
     patch('manual_csv.gamma_section.download_and_calculate',return_value={'basis':16.,'reference_date':'2026-11-20','bar_count':26,'minimum':15.,'maximum':17.,'last':16.,'source':'test'}), \\
     patch('manual_csv.gamma_section.serafini_cash_levels',return_value=None), \\
     patch('csv_gex_services._cached_gex',return_value={}):
    render()
"""
        at=AppTest.from_string(code,default_timeout=30).run()
        self.assertEqual(len(at.exception),0,str(at.exception))
        self.assertTrue(at.session_state["gamma_visual_extension_v1"])
        self.assertEqual(at.session_state["basis_override_2026-11-23"],"99")
        self.assertEqual(at.session_state["manual_csv_basis_override_2026-11-23"],"16")
        self.assertTrue(at.session_state["manual_csv_gamma_visual_extension_v1"])
        self.assertEqual(len(at.get("plotly_chart")),2)
        self.assertTrue(any("NET GAMMA · Call" in el.value for el in at.markdown))
        self.assertEqual(len(at.get("download_button")),1)
    def test_app_keeps_automatic_and_manual_independent(self):
        text=Path("app.py").read_text()
        self.assertLess(text.index("asia_panel = render(store, week, snaps, logs"),text.index("render_manual_csv_net_gamma()"))
        panel=Path("manual_csv/panel.py").read_text()
        self.assertNotIn("storage import",panel)
        self.assertNotIn("st.session_state[f\"serafini_selected_snapshot_",panel)
        self.assertNotIn("render_lab(",Path("manual_csv/gamma_section.py").read_text())

import unittest
from datetime import date
from pathlib import Path
from net_gamma import profile, intelligent_visual_profile, gamma_component_totals
from spx_csv_gex_adapter import analyze_alpaca_snapshot
from csv_gex_core import gex

class AlignmentTests(unittest.TestCase):
    def test_canonical_totals_and_extension(self):
        day=date(2026,11,24)
        rows=[]
        for strike in range(6500,7510,5):
            for side,vol in [('C',20),('P',10)]:
                rows.append({'option':f'SPXW{day:%y%m%d}{side}{strike*1000:08d}',
                             'gamma':.001, 'open_interest':100, 'volume':vol})
        snap={'data':{'options':rows}}
        core=profile(snap,day,7000)
        viz=intelligent_visual_profile(snap,core,day,7000,7000)
        self.assertEqual(len(viz['rows']),50)
        extended=intelligent_visual_profile(snap,core,day,7000,7250)
        self.assertGreater(len(extended['rows']),50)
        self.assertEqual(len(core['rows']),50)
        self.assertAlmostEqual(gamma_component_totals([core],require_core=True)['net'],.5)
        with self.assertRaises(ValueError):
            gamma_component_totals([extended],require_core=True)
    def test_alpaca_coverage_is_never_certified(self):
        d=date(2026,11,24)
        doc={'symbol':'_SPX','source_provenance':{
             'source':'ALPACA_INDICATIVE_OCC_OI','kind':'DERIVED_ALPACA_INDICATIVE_NOT_CBOE',
             'source_quote_latest_utc':'2026-11-24T17:59:58+00:00',
             'download_utc':'2026-11-24T18:00:00+00:00',
             'expiry_quality':[{'rejected':{'volume_missing':3}}],
             },'data':{'current_price':7200.,'options':[]}}
        for expiry in (d,date(2026,11,27)):
            for strike in range(7000,7405,5):
                for side in ('C','P'):
                    doc['data']['options'].append({'option':f'SPXW{expiry:%y%m%d}{side}{strike*1000:08d}',
                        'open_interest':100, 'iv':.25, 'oi_source':'OCC_SERIES_SEARCH'})
        info=analyze_alpaca_snapshot(doc,d)
        self.assertFalse(info['quality_certified'])
        self.assertEqual(info['source'],'ALPACA_INDICATIVE_OCC_OI')
        for report in info['reports'].values():
            self.assertEqual(report['coverage_scope'],'RETAINED_CONTRACTS_ONLY')
            self.assertFalse(report['quality_certified'])
            self.assertEqual(report['status'],'DIAGNOSTICO')
            self.assertAlmostEqual(report['net_gex'],report['call_gex']+report['put_gex'],delta=.01)
            self.assertEqual(len(report['curve']),201)
        doc['source_provenance']['source']='CBOE'
        with self.assertRaises(ValueError):
            analyze_alpaca_snapshot(doc,d)
    def test_manual_gex_without_automatic_snapshot(self):
        from unittest.mock import patch
        from spx_csv_gex_section import render
        manual={"payload":b"csv", "symbol":"SPX", "es_spx_basis":16.0}
        with patch("spx_csv_gex_section.st") as st, patch("spx_csv_gex_section.render_csv") as show, patch("spx_csv_gex_section._selected_snapshot") as archived:
            st.session_state={}
            render("2026-W48", None, manual_input=manual)
            show.assert_called_once_with(b"csv","SPX",es_spx_basis=16.0,state_prefix="manual_")
            archived.assert_not_called()

    def test_manual_gex_selected_even_with_automatic_snapshot(self):
        from unittest.mock import patch
        from spx_csv_gex_section import render
        manual={"payload":b"other", "symbol":"NDX", "es_spx_basis":None}
        with patch("spx_csv_gex_section.st") as st, patch("spx_csv_gex_section.render_csv") as show, patch("spx_csv_gex_section._selected_snapshot") as archived:
            st.session_state={"gamma_compare_context_2026-W48":{
                "snapshot":"SPX_ALPACA_20261125_150000.json.gz","offset":16}}
            st.radio.return_value="CSV manuale"
            render("2026-W48",None,manual_input=manual)
            show.assert_called_once_with(b"other","NDX",es_spx_basis=None,state_prefix="manual_")
            archived.assert_not_called()

    def test_module_imports_and_binding(self):
        import csv_gex_panel, spx_csv_gex_section
        self.assertTrue(callable(csv_gex_panel.render_report))
        self.assertTrue(callable(spx_csv_gex_section.render))
        s=Path('gamma_section.py').read_text()
        self.assertIn('computed_profiles.values(), require_core=True',s)
        self.assertIn('Alpaca indicative · ultima quotazione',s)
        self.assertIn('intelligent_visual_profile(',s)

if __name__=='__main__':
    unittest.main()

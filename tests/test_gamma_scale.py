import copy
from datetime import date
from pathlib import Path
import textwrap
import unittest

import plotly.graph_objects as go
from gamma_scale import profile_gamma_bounds, gamma_components
from pine_export import make_pine


def profile(values):
    return {'expiry': '2026-10-27', 'rows': [
        {'strike_spx': 7700 + 5*i, 'net_gamma': v,
         'call_gamma_component': max(v, 0) + 100,
         'put_gamma_component': max(-v, 0) + 100}
        for i, v in enumerate(values)]}


class GammaScaleTest(unittest.TestCase):
    def test_shared_bounds_include_both_profiles_and_zero(self):
        a, b = profile([-200, 400]), profile([-40, 20])
        original = copy.deepcopy([a, b])
        self.assertEqual(profile_gamma_bounds([a, b]), [-248, 448])
        self.assertEqual([a, b], original)
        self.assertNotEqual(profile_gamma_bounds([a]), profile_gamma_bounds([b]))

    def test_empty_zero_and_one_sided_profiles(self):
        self.assertEqual(profile_gamma_bounds([]), [-0.08, 0.08])
        self.assertEqual(profile_gamma_bounds([profile([0])]), [-0.08, 0.08])
        self.assertLess(profile_gamma_bounds([profile([5])])[0], 0)
        self.assertGreater(profile_gamma_bounds([profile([-5])])[1], 0)
        with self.assertRaises(ValueError):
            profile_gamma_bounds([profile([float('nan')])])

    def test_compensation_is_visible_without_changing_net(self):
        for call, put in [(100, 95), (6, 1)]:
            row = {'call_gamma_component': call, 'put_gamma_component': put}
            components = gamma_components(row)
            self.assertEqual(components, [call, -put])
            self.assertEqual(sum(components), 5)

    def test_actual_dashboard_chart_block_uses_shared_range(self):
        source = Path('gamma_section.py').read_text()
        start = source.index('            fig = go.Figure()', source.index('    profile_errors = {}'))
        end = source.index('            st.plotly_chart(fig', start)
        chart_code = compile(textwrap.dedent(source[start:end]), 'chart_block', 'exec')
        profiles = [profile([-200, 400]), profile([-40, 20])]
        shared = profile_gamma_bounds(profiles)
        for independent in [False, True]:
            figures = []
            for result in profiles:
                records = result['rows']
                env = dict(go=go, result=result, records=records,
                           y=[r['strike_spx'] for r in records],
                           net=[r['net_gamma'] for r in records], colors=['red', 'green'],
                           title='test', day=date(2026, 10, 27), capture_info='test', offset=0,
                           independent_scale=independent, shared_bounds=shared,
                           profile_gamma_bounds=profile_gamma_bounds, gamma_components=gamma_components)
                exec(chart_code, env)
                fig = env['fig']
                expected = profile_gamma_bounds([result]) if independent else shared
                self.assertEqual(list(fig.layout.xaxis.range), expected)
                self.assertEqual(fig.layout.xaxis.fixedrange, not independent)
                self.assertEqual(list(fig.data[0].x), env['net'])
                figures.append(fig)
            if not independent:
                self.assertEqual(figures[0].layout.xaxis.range, figures[1].layout.xaxis.range)

    def test_pine_export_keeps_one_effective_scale_for_both(self):
        pine = make_pine(profile([-200, 400]), profile([-40, 20]), 0, '2026-10-27', 'test')
        self.assertIn('math.max(requiredScale(odteValues, offsetOdte), requiredScale(weeklyValues, offsetWeekly))', pine)
        calls = [line for line in pine.splitlines() if line.strip().startswith('renderProfile(')]
        self.assertEqual(len(calls), 3)  # definition and the two render calls
        for call in calls[1:]:
            self.assertTrue(call.endswith('actualScale, adjusted)'))
        self.assertIn('scala comune', pine)


if __name__ == '__main__':
    unittest.main()

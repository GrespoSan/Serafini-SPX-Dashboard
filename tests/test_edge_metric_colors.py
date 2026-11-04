"""GEX Edge metric colors are based on their current modeled sign."""
import ast
from html import escape
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EDGE = (ROOT / "edge_triptych.py").read_text(encoding="utf-8")


class FakeColumn:
    def __init__(self):
        self.rendered = None
        self.unsafe_allow_html = False

    def markdown(self, markup, unsafe_allow_html=False):
        self.rendered = markup
        self.unsafe_allow_html = unsafe_allow_html


def load_metric_functions():
    source = ast.parse(EDGE, filename="edge_triptych.py")
    selected = [
        node for node in source.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_edge_metric_color", "_colored_edge_metric")
    ]
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    scope = {"escape": escape, "math": math}
    exec(compile(module, "edge_triptych.py", "exec"), scope)
    return scope


class EdgeMetricColorsTest(unittest.TestCase):
    def setUp(self):
        self.fun = load_metric_functions()

    def test_positive_negative_and_neutral_colors(self):
        color = self.fun["_edge_metric_color"]
        self.assertEqual(color("positive"), "#22b573")
        self.assertEqual(color("negative"), "#e64b4b")
        self.assertEqual(color("Positive"), "#22b573")
        self.assertEqual(color("Negative"), "#e64b4b")
        self.assertEqual(color(8.91e9), "#22b573")
        self.assertEqual(color(-5.73e9), "#e64b4b")
        for value in (0, "Zero", None, float("nan")):
            with self.subTest(value=value):
                self.assertEqual(color(value), "inherit")

    def test_displayed_value_is_colored_and_html_escaped(self):
        column = FakeColumn()
        self.fun["_colored_edge_metric"](
            column, "NET GEX", "+8.91 mld USD", 8.91e9,
        )
        self.assertIn("color:#22b573", column.rendered)
        self.assertIn("+8.91 mld USD", column.rendered)
        self.assertTrue(column.unsafe_allow_html)
        self.fun["_colored_edge_metric"](
            column, "Segno Gamma modellato", "<negative>", "negative",
        )
        self.assertIn("color:#e64b4b", column.rendered)
        self.assertIn("&lt;negative&gt;", column.rendered)
        self.assertNotIn("<negative>", column.rendered)

    def test_three_requested_metrics_use_value_color(self):
        self.assertIn('_colored_edge_metric(p[4], "Regime Edge"', EDGE)
        self.assertIn('columns[0], "Segno Gamma modellato"', EDGE)
        self.assertIn('columns[1], "NET GEX"', EDGE)
        self.assertNotIn("@st.fragment(run_every=", EDGE)


if __name__ == "__main__":
    unittest.main()

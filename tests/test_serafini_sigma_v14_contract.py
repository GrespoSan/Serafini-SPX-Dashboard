"""Minimal source-contract guard for the Pine v1.4 RTH sigma implementation."""
import ast
from pathlib import Path


def test_structure():
    path = Path(__file__).resolve().parents[1] / "serafini_sigma.py"
    ast.parse(path.read_text(encoding="utf-8"))

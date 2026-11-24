"""Only constants used by the pinned SPX-Edge-GEX-Lab pure model.

Values are sourced from gexlab/config.py, GrespoSan/SPX-Edge-GEX-Lab main
on 2026-11-21. Changes to the original project are not made here.
"""
from datetime import time
IV_MIN = 0.01
IV_MAX = 5.0
COVERAGE_STRIKE_BAND = 0.10
QUALITY_MIN_PCT = 95.0
GATE_OI_BAND = 0.05
GATE_OI_MIN_PCT = 95.0
GATE_GEX_MIN_PCT = 98.0
SWEEP_CHUNK = 256
SWEEP_STEP = {"SPY": 0.01, "QQQ": 0.01, "SPX": 0.5, "NDX": 1.0}
STOCK_STEP_FRAC = 0.01
SWEEP_BAND = 0.10
NEUTRAL_FRAC = 0.02
WALL_STRIKE_BAND = 0.10
PROFILE_POINTS = 201
DEFAULT_SETTLE_PM = time(16, 0)
DEFAULT_SETTLE_AM = time(9, 30)

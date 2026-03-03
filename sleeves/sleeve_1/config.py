# ============================================================
# SLEEVE 1 — Configuration
#
# All tunable parameters for Sleeve 1 live here.
# Mirrors the pattern established in sleeve_trend/config.py.
#
# LOCKED parameters: values are unchanged from backtest.py.
# This file is a structural extraction only — no logic was modified.
# ============================================================

import os

# ===== Equity & Sizing =====

INITIAL_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", "10000"))
MAX_POSITION_PCT = 0.07          # Max 7% NAV per position
MAX_SHORT_EXPOSURE_PCT = 0.05    # Max 5% NAV per short position

# ===== Hold Period Rules =====

MIN_HOLD_DAYS = 3
MAX_HOLD_DAYS = 5

# ===== Position Limits =====

TOP_LONGS = 3
MAX_SHORT_POSITIONS = 1

# ===== Signal Thresholds =====

LONG_THRESHOLD = 75       # Min signal to open a long
SHORT_THRESHOLD = 20      # Max signal to open a short
LONG_FLOOR_EXIT = 65      # Exit long if signal drops below this
SHORT_FLOOR_EXIT = 30     # Exit short if signal rises above this

# ===== Signal Crash / Emergency Exit =====

EXIT_SIGNAL_BUFFER = 5
SIGNAL_CRASH_EXIT_ENABLED = True
SIGNAL_CRASH_DROP = 20            # Exit if signal drops >20 pts intraday
SIGNAL_CRASH_ABS_FLOOR = 55       # Exit if signal falls below this floor

# ===== ATR-Based Stops =====

EMERGENCY_STOP_ATR_MULT = 2.25   # Hard stop at 2.25× ATR from entry
USE_ENTRY_ATR_FOR_STOP = True     # Use ATR at entry (not current) for stop
GAP_LABEL_ATR_MULT = 1.0          # Gap threshold for labeling (diagnostics only)

# ===== Transaction Costs =====

TRADE_COST = 0.50   # Fixed cost per trade leg ($0.50)

# ===== Volatility-Regime Rules =====
# Entry/exit thresholds and max hold period adapt to market vol regime.

DEFAULT_RULES = dict(
    LONG_THRESHOLD=LONG_THRESHOLD,
    LONG_FLOOR_EXIT=LONG_FLOOR_EXIT,
    MAX_HOLD_DAYS=MAX_HOLD_DAYS,
)

VOL_BUCKET_RULES = {
    "low":    dict(LONG_THRESHOLD=72, LONG_FLOOR_EXIT=64, MAX_HOLD_DAYS=7),
    "medium": dict(LONG_THRESHOLD=75, LONG_FLOOR_EXIT=65, MAX_HOLD_DAYS=5),
    "high":   dict(LONG_THRESHOLD=78, LONG_FLOOR_EXIT=66, MAX_HOLD_DAYS=4),
}

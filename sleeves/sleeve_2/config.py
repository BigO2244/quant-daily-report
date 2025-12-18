# ============================================================
# SLEEVE 2 — Valuation (P/E vs Industry + Trend) — v1.0
# ============================================================

SLEEVE_NAME = "sleeve_2"

# Universe
UNIVERSE_CSV = "data/universe.csv"

# Portfolio construction
TOP_LONGS = 3
TOP_SHORTS = 1  # long primarily, short only when valuation is extreme

# Thresholds / floors
LONG_THRESHOLD = 75
LONG_FLOOR_EXIT = 65
EXIT_SIGNAL_BUFFER = 5

# Holding periods
MIN_HOLD_DAYS = 3
MAX_HOLD_DAYS_LONG = 20
MAX_HOLD_DAYS_SHORT = 30

# Valuation extreme threshold (confirmed)
Z_EXTREME = 2.0

# Stricter threshold for initiating shorts (keep Z_EXTREME=2.0 for long exits)
Z_EXTREME_SHORT = 2.5

# Long entry valuation gate
Z_ENTRY_LONG = -0.5

# Avoid runaway P/E expansion on long entries (v1 guard)
PE_CHANGE_20D_MAX_LONG_ENTRY = 0.10

# Short exit mean-reversion threshold
Z_SHORT_EXIT_MEAN_REVERT = 1.0

# Industry requirements
MIN_INDUSTRY_COUNT = 5

# Capital parking
CASH_PROXY_TICKER = "SGOV"

# P/E policy
PE_PREFER_FORWARD = True
PE_ABS_CAP = 500.0

# Scoring weights
W_Z = 0.7
W_TREND = 0.3

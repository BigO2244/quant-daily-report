# ============================================================
# SLEEVE TREND v1 — Configuration (Revision B)
# Trend-following sleeve using EMA crossover + ADX filter
# 
# REVISION NOTES (2026-01-26 Draft B):
# - Added explicit sleeve-level volatility targeting
# - Added drawdown circuit breaker
# - Added gross/net exposure caps
# - Clarified all risk parameters
# ============================================================

import os

# ===== Backtest configuration (matching Sleeve 1 style) =====

INITIAL_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", "10000"))

# ============================================================
# SIGNAL PARAMETERS
# ============================================================

# Moving Average Periods
EMA_FAST = 20               # Fast EMA for crossover
EMA_SLOW = 50               # Slow EMA for crossover
EMA_TREND_FILTER = 200      # Long-term trend filter

# ADX Filter (Average Directional Index)
ADX_PERIOD = 14
ADX_THRESHOLD = 20          # Min ADX for entry (market is trending)
ADX_STRONG_TREND = 30       # Strong trend (higher conviction)

# ATR Parameters
ATR_PERIOD = 14

# Entry Confirmation
VOLUME_CONFIRM_MULT = 1.0   # Volume >= this × 20-day avg
REQUIRE_TREND_FILTER = True # Only long above 200 EMA, short below

# ============================================================
# POSITION LIMITS (HARD CAPS)
# ============================================================

TOP_LONGS = 3               # Max simultaneous long positions
TOP_SHORTS = 1              # Max simultaneous short positions  
MAX_POSITIONS = 4           # Total max positions (TOP_LONGS + TOP_SHORTS)

# Per-position caps
MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "0.10"))  # 10% max per position
MIN_POSITION_PCT = 0.02     # 2% min per position (avoid tiny positions)

# Sector diversification
MAX_POSITIONS_PER_SECTOR = 2  # Max 2 positions in same sector

# ============================================================
# RISK MANAGEMENT
# ============================================================

# Per-trade risk (ATR-based sizing)
RISK_PER_TRADE = float(os.environ.get("MAX_RISK_PCT_PER_TRADE", "0.01"))  # 1% risk per trade

# Stop loss and trailing stop
STOP_LOSS_ATR_MULT = 2.0        # Initial stop: 2× ATR from entry
TRAILING_STOP_ATR_MULT = 2.5    # Trailing stop: 2.5× ATR from high/low
PROFIT_TARGET_ATR_MULT = 6.0    # Profit target: 6× ATR (R:R = 3:1)

# ============================================================
# SLEEVE-LEVEL VOLATILITY TARGETING (NEW)
# ============================================================

# Target annualized volatility for the sleeve
TARGET_ANNUAL_VOL = 0.15        # 15% target annual volatility

# Volatility scaling bounds
VOL_SCALE_MIN = 0.5             # Min scale factor (half size)
VOL_SCALE_MAX = 1.5             # Max scale factor (1.5× size)

# Lookback for realized vol calculation
VOL_LOOKBACK_DAYS = 20          # 20-day realized vol

# ============================================================
# DRAWDOWN CIRCUIT BREAKER (NEW)
# ============================================================

# Max drawdown before reducing exposure
MAX_DRAWDOWN_SOFT = 0.10        # 10% DD: reduce position sizes by 50%
MAX_DRAWDOWN_HARD = 0.15        # 15% DD: stop new entries until recovery

# Recovery threshold (DD must recover to this level to resume)
DRAWDOWN_RECOVERY_PCT = 0.05    # Resume at 5% DD

# ============================================================
# EXPOSURE LIMITS (NEW)
# ============================================================

# Gross exposure (sum of |long| + |short|)
MAX_GROSS_EXPOSURE = 0.50       # 50% max gross exposure

# Net exposure (long - short)
MAX_NET_EXPOSURE = 0.40         # 40% max net long
MIN_NET_EXPOSURE = -0.20        # 20% max net short

# Cash buffer
MIN_CASH_PCT = 0.05             # Always keep 5% cash

# ============================================================
# HOLDING RULES
# ============================================================

MIN_HOLD_DAYS = 1               # Minimum 1 day hold (avoid same-day exit)
MAX_HOLD_DAYS = 60              # Maximum 60-day hold
EXIT_ON_REVERSE_CROSS = True    # Exit on reverse EMA crossover

# ============================================================
# SCORING THRESHOLDS (0-100 scale)
# ============================================================

LONG_THRESHOLD = 75             # Min score for long entry
SHORT_THRESHOLD = 75            # Min score for short entry
LONG_FLOOR_EXIT = 40            # Exit long if score drops below

# ============================================================
# TRANSACTION COSTS
# ============================================================

TRADE_COST = 0.50               # Fixed cost per trade ($0.50)
SLIPPAGE_PCT = 0.001            # 0.1% slippage assumption

# ============================================================
# LIQUIDITY FILTERS
# ============================================================

MIN_PRICE = 5.0                 # Min stock price ($5)
MIN_AVG_VOLUME = 100_000        # Min 20-day avg volume (100K shares)
VOLUME_LOOKBACK = 20            # Lookback for volume average

# ============================================================
# CASH PROXY & DATA
# ============================================================

CASH_PROXY_TICKER = "SGOV"      # Short-term treasury ETF for idle cash
UNIVERSE_CSV = "data/universe.csv"

# ============================================================
# BENCHMARK (for statistics)
# ============================================================

BENCHMARK_TICKER = "SPY"        # S&P 500 ETF for beta/correlation

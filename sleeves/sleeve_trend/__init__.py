"""
Sleeve Trend v1 - Trend Following Strategy (Revision B)
========================================================

REVISION B CHANGES (2026-01-26):
- Explicit t/t+1 execution semantics documentation
- Sleeve-level volatility targeting
- Drawdown circuit breaker
- Enhanced statistics (CAGR/Vol/Sharpe/Sortino/MaxDD/Turnover/Beta/Corr)

Interface matches Sleeve 1 for daily_quant_report.py compatibility:
    from sleeves.sleeve_trend.backtest import prepare_data, backtest, build_daily_sleeve_output

Strategy:
- Entry: EMA(20) crosses EMA(50) + ADX > 20 + price aligned with EMA(200)
- Exit: Trailing stop (2.5x ATR), profit target (6x ATR), or reverse cross
- Sizing: 1% risk per trade, ATR-based, with vol targeting adjustment
"""

from .backtest import (
    prepare_data,
    backtest,
    build_daily_sleeve_output,
    compute_stats,
    Position,
    RiskManager,
)

from .config import (
    INITIAL_EQUITY,
    EMA_FAST,
    EMA_SLOW,
    ADX_THRESHOLD,
    STOP_LOSS_ATR_MULT,
    TRAILING_STOP_ATR_MULT,
    MAX_HOLD_DAYS,
    TOP_LONGS,
    TOP_SHORTS,
    TARGET_ANNUAL_VOL,
    MAX_DRAWDOWN_SOFT,
    MAX_DRAWDOWN_HARD,
)

__version__ = "1.0.1"  # Revision B
__all__ = [
    'prepare_data',
    'backtest',
    'build_daily_sleeve_output',
    'compute_stats',
    'Position',
    'RiskManager',
]

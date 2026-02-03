# ============================================================
# SLEEVE TREND v1 — Trend Following (Revision B)
# EMA crossover + ADX filter with ATR-based stops
#
# REVISION NOTES (2026-01-26 Draft B):
# 1. Clarified t/t+1 execution semantics with explicit documentation
# 2. Added sleeve-level volatility targeting
# 3. Added drawdown circuit breaker
# 4. Added explicit position/exposure caps
# 5. Enhanced statistics (CAGR/Vol/Sharpe/Sortino/MaxDD/Turnover/Beta/Corr)
# ============================================================


import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402
from typing import Tuple, Dict, List

from core.quant_report import (  # noqa: E402
    TICKERS,
    download_prices,
)

from . import config as cfg  # noqa: E402
from .indicators import compute_trend_indicators  # noqa: E402

print("Backtest Version Check: Sleeve-Trend-v1-2026-01-26-RevB")
assert __name__.startswith("sleeves.sleeve_trend"), "Invalid import context for Sleeve Trend"

# ============================================================
# EXECUTION TIMING DOCUMENTATION
# ============================================================
"""
CRITICAL: Signal Generation vs Trade Execution

This backtest implements standard institutional execution timing:

    Day T (at CLOSE):
        1. All indicators computed using OHLCV up to and including day T
        2. Entry/exit signals evaluated using close(T) data
        3. Pending orders queued for next day execution

    Day T+1 (at OPEN):
        1. Pending exit orders execute at open(T+1)
        2. Pending entry orders execute at open(T+1)
        3. Position sizes calculated using equity at close(T)

This avoids look-ahead bias: we never use future data to make decisions.
The signal "sees" data up to close(T), but execution happens at open(T+1).
"""


# ============================================================
# POSITION CLASS
# ============================================================


class Position:
    """
    Tracks a single position with entry details and trailing stop state.
    Matches Sleeve 1 interface.
    """

    def __init__(
        self,
        ticker: str,
        direction: int,
        shares: int,
        entry_price: float,
        entry_date: pd.Timestamp,
        entry_atr: float = None,
        entry_signal: float = None,
        sector: str = None,
    ):
        self.ticker = ticker
        self.direction = direction  # 1 = long, -1 = short
        self.shares = shares
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.entry_atr = entry_atr
        self.entry_signal = entry_signal
        self.sector = sector
        self.hold_days = 0
        self.highest_price = entry_price  # For trailing stop (longs)
        self.lowest_price = entry_price  # For trailing stop (shorts)

    @property
    def notional(self) -> float:
        return self.shares * self.entry_price


# ============================================================
# RISK MANAGEMENT UTILITIES
# ============================================================


class RiskManager:
    """
    Manages sleeve-level risk including:
    - Volatility targeting
    - Drawdown circuit breaker
    - Exposure limits
    """

    def __init__(self, initial_equity: float):
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.in_drawdown_mode = False
        self.recent_returns: List[float] = []

    def update(self, current_equity: float, daily_return: float = 0.0):
        """Update risk state after each day."""
        # Track peak for drawdown
        self.peak_equity = max(self.peak_equity, current_equity)

        # Track returns for vol calculation
        self.recent_returns.append(daily_return)
        if len(self.recent_returns) > cfg.VOL_LOOKBACK_DAYS:
            self.recent_returns.pop(0)

    @property
    def current_drawdown(self) -> float:
        """Current drawdown from peak (as positive number)."""
        if self.peak_equity <= 0:
            return 0.0
        return 0.0  # Deprecated; use get_drawdown(current_equity)

    def get_drawdown(self, current_equity: float) -> float:
        """Calculate drawdown from peak."""
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - current_equity) / self.peak_equity)

    def get_realized_vol(self) -> float:
        """Get annualized realized volatility."""
        if len(self.recent_returns) < 5:
            return cfg.TARGET_ANNUAL_VOL  # Default to target
        return np.std(self.recent_returns) * np.sqrt(252)

    def get_vol_scale_factor(self) -> float:
        """
        Calculate position scale factor based on realized vol vs target.
        If vol is high, reduce size. If vol is low, can increase size.
        """
        realized = self.get_realized_vol()
        if realized <= 0:
            return 1.0

        # Scale = target / realized, bounded
        scale = cfg.TARGET_ANNUAL_VOL / realized
        return np.clip(scale, cfg.VOL_SCALE_MIN, cfg.VOL_SCALE_MAX)

    def check_drawdown_circuit_breaker(
        self, current_equity: float
    ) -> Tuple[bool, float]:
        """
        Check if drawdown circuit breaker is triggered.

        Returns:
            (allow_new_entries: bool, size_multiplier: float)
        """
        dd = self.get_drawdown(current_equity)

        # Hard stop: no new entries
        if dd >= cfg.MAX_DRAWDOWN_HARD:
            self.in_drawdown_mode = True
            return False, 0.0

        # Soft reduction: halve position sizes
        if dd >= cfg.MAX_DRAWDOWN_SOFT:
            self.in_drawdown_mode = True
            return True, 0.5

        # Recovery: check if we're back below recovery threshold
        if self.in_drawdown_mode and dd <= cfg.DRAWDOWN_RECOVERY_PCT:
            self.in_drawdown_mode = False

        return True, 1.0

    def check_exposure_limits(
        self, positions: Dict[str, Position], equity: float
    ) -> Tuple[float, float]:
        """
        Calculate current gross and net exposure.

        Returns:
            (gross_exposure, net_exposure)
        """
        if equity <= 0:
            return 0.0, 0.0

        long_exposure = sum(
            p.shares * p.entry_price for p in positions.values() if p.direction == 1
        )
        short_exposure = sum(
            p.shares * p.entry_price for p in positions.values() if p.direction == -1
        )

        gross = (long_exposure + short_exposure) / equity
        net = (long_exposure - short_exposure) / equity

        return gross, net


# ============================================================
# DATA PREPARATION
# ============================================================


def prepare_data(period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """
    Download prices and compute trend signals.

    Returns DataFrame with: date, ticker, OHLCV, atr, adx, signals, sector
    """
    # Download prices using existing platform utility
    prices = download_prices(TICKERS, period=period, interval=interval)

    if prices.empty:
        raise ValueError("No price data downloaded")

    # Load sector mapping from universe
    try:
        universe = pd.read_csv(cfg.UNIVERSE_CSV)
        universe["ticker"] = universe["ticker"].str.upper()
        sector_map = universe.set_index("ticker")["sector"].to_dict()
    except Exception:
        sector_map = {}

    # Compute indicators per ticker
    results = []
    for ticker, group in prices.groupby("ticker"):
        group = group.sort_values("date").reset_index(drop=True)
        with_indicators = compute_trend_indicators(group)
        with_indicators["sector"] = sector_map.get(ticker, "Unknown")
        results.append(with_indicators)

    signals = pd.concat(results, ignore_index=True)

    # Apply liquidity filters
    signals["passes_liquidity"] = (signals["close"] >= cfg.MIN_PRICE) & (
        signals["volume_sma"] >= cfg.MIN_AVG_VOLUME
    )

    # Build entry signals (evaluated at CLOSE of day t)
    # Long: golden cross + above 200 EMA + ADX > threshold + volume confirm
    signals["signal_long"] = (
        signals["golden_cross"]
        & (signals["above_trend"] if cfg.REQUIRE_TREND_FILTER else True)
        & (signals["adx"] >= cfg.ADX_THRESHOLD)
        & (signals["volume_ratio"] >= cfg.VOLUME_CONFIRM_MULT)
        & signals["passes_liquidity"]
    )

    # Short: death cross + below 200 EMA + ADX > threshold + volume confirm
    signals["signal_short"] = (
        signals["death_cross"]
        & (signals["below_trend"] if cfg.REQUIRE_TREND_FILTER else True)
        & (signals["adx"] >= cfg.ADX_THRESHOLD)
        & (signals["volume_ratio"] >= cfg.VOLUME_CONFIRM_MULT)
        & signals["passes_liquidity"]
    )

    # Compute entry score (0-100 scale) for ranking candidates
    def rank_pct(x):
        return x.rank(pct=True) * 100

    signals["adx_score"] = signals.groupby("date")["adx"].transform(rank_pct)
    signals["trend_dist"] = (
        (signals["close"] - signals["ema_trend"]) / signals["ema_trend"] * 100
    )
    signals["trend_score"] = signals.groupby("date")["trend_dist"].transform(
        lambda x: x.abs().rank(pct=True) * 100
    )

    # Final signal: ADX strength (60%) + trend alignment (40%)
    signals["final_signal"] = (
        0.6 * signals["adx_score"] + 0.4 * signals["trend_score"]
    ).clip(0, 100)

    signals["date"] = pd.to_datetime(signals["date"])
    return signals.sort_values(["date", "ticker"])


# ============================================================
# POSITION SIZING
# ============================================================


def calculate_shares(
    equity: float,
    price: float,
    atr: float,
    vol_scale: float = 1.0,
    dd_scale: float = 1.0,
) -> int:
    """
    ATR-based position sizing with volatility and drawdown scaling.

    Args:
        equity: Current account equity
        price: Entry price
        atr: ATR at entry
        vol_scale: Volatility targeting multiplier (0.5-1.5)
        dd_scale: Drawdown circuit breaker multiplier (0.5 or 1.0)

    Returns:
        Number of shares (integer)
    """
    if price <= 0 or atr <= 0 or equity <= 0:
        return 0

    # Base risk sizing: risk RISK_PER_TRADE of equity
    risk_amount = equity * cfg.RISK_PER_TRADE
    stop_distance = cfg.STOP_LOSS_ATR_MULT * atr

    if stop_distance <= 0:
        return 0

    shares_by_risk = risk_amount / stop_distance

    # Apply vol and drawdown scaling
    shares_scaled = shares_by_risk * vol_scale * dd_scale

    # Apply position size caps
    max_notional = equity * cfg.MAX_POSITION_PCT
    min_notional = equity * cfg.MIN_POSITION_PCT

    shares_by_max = max_notional / price
    shares_by_min = min_notional / price

    # Final shares: bounded between min and max
    shares = int(min(shares_scaled, shares_by_max))

    # If below minimum, don't enter (avoid tiny positions)
    if shares < shares_by_min:
        return 0

    return shares


# ============================================================
# EXIT CONDITION CHECKER
# ============================================================


def check_exit(pos: Position, row: pd.Series) -> Tuple[bool, str]:
    """
    Check if position should exit based on CLOSE data of day t.
    If True, exit will execute at OPEN of day t+1.

    Returns:
        (should_exit: bool, reason: str)
    """
    direction = pos.direction
    entry_price = pos.entry_price
    entry_atr = pos.entry_atr or row["atr"]

    _ = row["close"]
    current_high = row["high"]
    current_low = row["low"]

    # Update trailing stop reference prices
    if direction == 1:
        pos.highest_price = max(pos.highest_price, current_high)
    else:
        pos.lowest_price = min(pos.lowest_price, current_low)

    # 1. Max holding period
    if pos.hold_days >= cfg.MAX_HOLD_DAYS:
        return True, "max_hold_days"

    if direction == 1:  # Long position
        # Initial stop loss (checked intraday)
        initial_stop = entry_price - (cfg.STOP_LOSS_ATR_MULT * entry_atr)
        if current_low <= initial_stop:
            return True, "initial_stop_long"

        # Trailing stop (from highest price since entry)
        if pos.hold_days > 0:
            trailing_stop = pos.highest_price - (cfg.TRAILING_STOP_ATR_MULT * entry_atr)
            if current_low <= trailing_stop:
                return True, "trailing_stop_long"

        # Profit target
        if cfg.PROFIT_TARGET_ATR_MULT:
            target = entry_price + (cfg.PROFIT_TARGET_ATR_MULT * entry_atr)
            if current_high >= target:
                return True, "profit_target_long"

        # Reverse crossover (death cross while long)
        if cfg.EXIT_ON_REVERSE_CROSS and row.get("death_cross", False):
            return True, "reverse_cross_long"

    else:  # Short position
        # Initial stop loss
        initial_stop = entry_price + (cfg.STOP_LOSS_ATR_MULT * entry_atr)
        if current_high >= initial_stop:
            return True, "initial_stop_short"

        # Trailing stop (from lowest price since entry)
        if pos.hold_days > 0:
            trailing_stop = pos.lowest_price + (cfg.TRAILING_STOP_ATR_MULT * entry_atr)
            if current_high >= trailing_stop:
                return True, "trailing_stop_short"

        # Profit target
        if cfg.PROFIT_TARGET_ATR_MULT:
            target = entry_price - (cfg.PROFIT_TARGET_ATR_MULT * entry_atr)
            if current_low <= target:
                return True, "profit_target_short"

        # Reverse crossover (golden cross while short)
        if cfg.EXIT_ON_REVERSE_CROSS and row.get("golden_cross", False):
            return True, "reverse_cross_short"

    return False, ""


# ============================================================
# MAIN BACKTEST ENGINE
# ============================================================


def backtest(signals_df: pd.DataFrame = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run backtest with t/t+1 execution semantics.

    EXECUTION FLOW:
        For each day t in [0, T]:
            1. EXECUTE pending orders from day t-1 at OPEN(t)
            2. UPDATE hold days for existing positions
            3. MARK-TO-MARKET at CLOSE(t)
            4. EVALUATE signals at CLOSE(t) -> queue for day t+1

    Returns:
        (equity_df, trades_df) matching Sleeve 1 interface
    """
    if signals_df is None:
        signals_df = prepare_data()

    # Build price lookup: (date, ticker) -> row
    price_table = signals_df.set_index(["date", "ticker"])
    dates = sorted(signals_df["date"].unique())

    # Initialize state
    equity = cfg.INITIAL_EQUITY
    positions: Dict[str, Position] = {}
    risk_mgr = RiskManager(cfg.INITIAL_EQUITY)

    # Output collectors
    equity_curve = []
    trades = []

    # Pending orders (signal at t, execute at t+1)
    pending_entries: List[Tuple] = []  # (ticker, direction, score, atr, sector)
    pending_exits: List[Tuple] = []  # (ticker, reason)

    prev_equity = cfg.INITIAL_EQUITY

    for i, current_date in enumerate(dates):

        # ============================================================
        # PHASE 1: EXECUTE pending orders at TODAY's OPEN
        # (Orders were generated yesterday at close)
        # ============================================================

        # Process exits FIRST (frees up capital and position slots)
        for ticker, reason in pending_exits:
            if ticker not in positions:
                continue

            pos = positions[ticker]

            try:
                row = price_table.loc[(current_date, ticker)]
                exit_price = row["open"]  # Execute at OPEN
            except KeyError:
                continue

            # Calculate realized P&L
            if pos.direction == 1:
                pnl = pos.shares * (exit_price - pos.entry_price)
            else:
                pnl = pos.shares * (pos.entry_price - exit_price)

            # Deduct round-trip transaction cost
            pnl -= 2 * cfg.TRADE_COST

            # Apply slippage
            slippage_cost = pos.shares * exit_price * cfg.SLIPPAGE_PCT
            pnl -= slippage_cost

            equity += pnl

            trades.append(
                {
                    "ticker": ticker,
                    "direction": pos.direction,
                    "shares": pos.shares,
                    "entry_date": pos.entry_date,
                    "exit_date": current_date,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "hold_days": pos.hold_days,
                    "exit_reason": reason,
                    "sector": pos.sector,
                }
            )

            del positions[ticker]

        pending_exits = []

        # Process entries (use equity AFTER exits)
        pending_entries.sort(key=lambda x: x[2], reverse=True)  # Sort by score

        current_longs = sum(1 for p in positions.values() if p.direction == 1)
        current_shorts = sum(1 for p in positions.values() if p.direction == -1)

        # Check risk limits
        allow_entries, dd_scale = risk_mgr.check_drawdown_circuit_breaker(equity)
        vol_scale = risk_mgr.get_vol_scale_factor()
        gross_exp, net_exp = risk_mgr.check_exposure_limits(positions, equity)

        # Sector tracking for diversification
        sector_counts = {}
        for p in positions.values():
            sector_counts[p.sector] = sector_counts.get(p.sector, 0) + 1

        for ticker, direction, score, entry_atr, sector in pending_entries:
            # Skip if already in position
            if ticker in positions:
                continue

            # Check if entries allowed (drawdown circuit breaker)
            if not allow_entries:
                continue

            # Check position count limits
            if direction == 1 and current_longs >= cfg.TOP_LONGS:
                continue
            if direction == -1 and current_shorts >= cfg.TOP_SHORTS:
                continue
            if len(positions) >= cfg.MAX_POSITIONS:
                continue

            # Check sector limit
            if sector_counts.get(sector, 0) >= cfg.MAX_POSITIONS_PER_SECTOR:
                continue

            # Check exposure limits
            if gross_exp >= cfg.MAX_GROSS_EXPOSURE:
                continue
            if direction == 1 and net_exp >= cfg.MAX_NET_EXPOSURE:
                continue
            if direction == -1 and net_exp <= cfg.MIN_NET_EXPOSURE:
                continue

            # Get entry price (OPEN of today)
            try:
                row = price_table.loc[(current_date, ticker)]
                entry_price = row["open"]
            except KeyError:
                continue

            if entry_price <= 0 or pd.isna(entry_price):
                continue

            # Calculate position size with scaling
            shares = calculate_shares(
                equity, entry_price, entry_atr, vol_scale, dd_scale
            )
            if shares <= 0:
                continue

            # Check cash availability (keep MIN_CASH_PCT buffer)
            cost = shares * entry_price + cfg.TRADE_COST
            available_cash = equity * (1 - cfg.MIN_CASH_PCT)
            if cost > available_cash:
                # Try smaller size
                shares = int(available_cash / entry_price) - 1
                if shares <= 0:
                    continue

            # Create position
            positions[ticker] = Position(
                ticker=ticker,
                direction=direction,
                shares=shares,
                entry_price=entry_price,
                entry_date=current_date,
                entry_atr=entry_atr,
                entry_signal=score,
                sector=sector,
            )

            # Deduct entry cost (commission only, notional stays in equity calc)
            equity -= cfg.TRADE_COST

            # Update counters
            if direction == 1:
                current_longs += 1
            else:
                current_shorts += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

            # Update exposure
            gross_exp, net_exp = risk_mgr.check_exposure_limits(positions, equity)

        pending_entries = []

        # ============================================================
        # PHASE 2: UPDATE hold days
        # ============================================================

        for pos in positions.values():
            pos.hold_days += 1

        # ============================================================
        # PHASE 3: MARK-TO-MARKET at CLOSE
        # ============================================================

        positions_mtm = 0.0
        for ticker, pos in positions.items():
            try:
                row = price_table.loc[(current_date, ticker)]
                current_price = row["close"]
            except KeyError:
                current_price = pos.entry_price

            if pos.direction == 1:
                # Long: current value
                positions_mtm += pos.shares * current_price
            else:
                # Short: entry_notional + unrealized P&L
                unrealized_pnl = pos.shares * (pos.entry_price - current_price)
                positions_mtm += pos.shares * pos.entry_price + unrealized_pnl

        # Total equity = cash (after realized P&L) + positions mark-to-market
        # Simplified: we track equity as cash + sum of notional at entry
        # MTM adjustment = positions_mtm - sum(entry_notional)
        entry_notional_sum = sum(p.shares * p.entry_price for p in positions.values())
        mtm_equity = equity + (positions_mtm - entry_notional_sum)

        # Update risk manager
        daily_return = (mtm_equity / prev_equity - 1) if prev_equity > 0 else 0.0
        risk_mgr.update(mtm_equity, daily_return)
        prev_equity = mtm_equity

        equity_curve.append(
            {
                "date": current_date,
                "equity": mtm_equity,
                "cash": equity - entry_notional_sum,
                "positions_value": positions_mtm,
                "num_positions": len(positions),
                "gross_exposure": gross_exp,
                "net_exposure": net_exp,
                "drawdown": risk_mgr.get_drawdown(mtm_equity),
            }
        )

        # ============================================================
        # PHASE 4: EVALUATE signals at CLOSE -> queue for tomorrow
        # ============================================================

        today_data = signals_df[signals_df["date"] == current_date]

        # Check exits for existing positions
        for ticker in list(positions.keys()):
            pos = positions[ticker]

            try:
                row = price_table.loc[(current_date, ticker)]
            except KeyError:
                continue

            should_exit, reason = check_exit(pos, row)

            if should_exit:
                pending_exits.append((ticker, reason))

        # Find new entry candidates
        for _, row in today_data.iterrows():
            ticker = row["ticker"]

            if ticker in positions:
                continue

            # Check for long signal
            if row["signal_long"] and row["final_signal"] >= cfg.LONG_THRESHOLD:
                pending_entries.append(
                    (
                        ticker,
                        1,  # direction
                        row["final_signal"],
                        row["atr"],
                        row["sector"],
                    )
                )

            # Check for short signal
            elif row["signal_short"] and row["final_signal"] >= cfg.SHORT_THRESHOLD:
                pending_entries.append(
                    (
                        ticker,
                        -1,  # direction
                        row["final_signal"],
                        row["atr"],
                        row["sector"],
                    )
                )

    # Build output DataFrames
    equity_df = pd.DataFrame(equity_curve)
    trades_df = (
        pd.DataFrame(trades)
        if trades
        else pd.DataFrame(
            columns=[
                "ticker",
                "direction",
                "shares",
                "entry_date",
                "exit_date",
                "entry_price",
                "exit_price",
                "pnl",
                "hold_days",
                "exit_reason",
                "sector",
            ]
        )
    )

    return equity_df, trades_df


# ============================================================
# REPORTING OUTPUT (matches Sleeve 1)
# ============================================================


def build_daily_sleeve_output(
    equity_df: pd.DataFrame, trades_df: pd.DataFrame, sleeve_name: str
) -> pd.DataFrame:
    """
    Canonical daily sleeve output for reporting/aggregation.
    Matches Sleeve 1 interface exactly.
    """
    df = equity_df.copy()
    df["sleeve"] = sleeve_name
    df["daily_return"] = df["equity"].pct_change().fillna(0.0)

    # Use pre-computed exposure if available, else default
    if "gross_exposure" not in df.columns:
        df["gross_exposure"] = 0.0
    if "net_exposure" not in df.columns:
        df["net_exposure"] = 0.0
    if "num_positions" not in df.columns:
        df["num_positions"] = 0

    return df[
        [
            "date",
            "sleeve",
            "equity",
            "daily_return",
            "gross_exposure",
            "net_exposure",
            "num_positions",
        ]
    ]


# ============================================================
# STATISTICS (Enhanced for Revision B)
# ============================================================


def compute_stats(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    benchmark_returns: pd.Series = None,
) -> dict:
    """
    Compute comprehensive backtest statistics.

    Returns dict with: CAGR, Vol, Sharpe, Sortino, MaxDD, Turnover, Beta, Corr
    """
    print("\n" + "=" * 60)
    print("SLEEVE TREND v1 — BACKTEST RESULTS (Revision B)")
    print("=" * 60)

    if equity_df.empty:
        print("No equity data")
        return {}

    # Basic metrics
    start_equity = cfg.INITIAL_EQUITY
    final_equity = equity_df["equity"].iloc[-1]
    n_days = len(equity_df)
    years = n_days / 252

    # Returns series
    returns = equity_df["equity"].pct_change().dropna()

    # CAGR
    total_return = final_equity / start_equity - 1
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    # Volatility (annualized)
    daily_vol = returns.std()
    annual_vol = daily_vol * np.sqrt(252)

    # Sharpe Ratio (assuming 5% risk-free rate)
    rf_daily = 0.05 / 252
    excess_returns = returns - rf_daily
    sharpe = (
        (excess_returns.mean() / returns.std()) * np.sqrt(252)
        if returns.std() > 0
        else 0
    )

    # Sortino Ratio (downside vol only)
    downside_returns = returns[returns < 0]
    downside_vol = (
        downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0.001
    )
    sortino = (cagr - 0.05) / downside_vol if downside_vol > 0 else 0

    # Max Drawdown
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdowns = (cumulative - rolling_max) / rolling_max
    max_dd = drawdowns.min()

    # Turnover (annualized)
    if not trades_df.empty:
        total_traded = trades_df["shares"].abs().sum() * trades_df["entry_price"].mean()
        avg_equity = equity_df["equity"].mean()
        turnover = (total_traded / avg_equity) * (252 / n_days) if avg_equity > 0 else 0
    else:
        turnover = 0

    # Beta and Correlation to benchmark
    beta = np.nan
    corr = np.nan
    if benchmark_returns is not None and len(benchmark_returns) > 0:
        # Align dates
        aligned = pd.DataFrame(
            {"sleeve": returns, "benchmark": benchmark_returns}
        ).dropna()
        if len(aligned) > 20:
            cov = aligned["sleeve"].cov(aligned["benchmark"])
            var_bench = aligned["benchmark"].var()
            beta = cov / var_bench if var_bench > 0 else 0
            corr = aligned["sleeve"].corr(aligned["benchmark"])

    # Print results
    print("\nPerformance Summary:")
    print(f"  Starting Equity:    ${start_equity:>12,.2f}")
    print(f"  Final Equity:       ${final_equity:>12,.2f}")
    print(f"  Total Return:       {total_return:>12.2%}")
    print(f"  CAGR:               {cagr:>12.2%}")
    print(f"  Annual Volatility:  {annual_vol:>12.2%}")
    print(f"  Sharpe Ratio:       {sharpe:>12.2f}")
    print(f"  Sortino Ratio:      {sortino:>12.2f}")
    print(f"  Max Drawdown:       {max_dd:>12.2%}")
    print(f"  Turnover (annual):  {turnover:>12.1f}x")
    if not np.isnan(beta):
        print(f"  Beta to SPY:        {beta:>12.2f}")
        print(f"  Correlation to SPY: {corr:>12.2f}")

    print("\nTrading Statistics:")
    print(f"  Trading Days:       {n_days:>12}")
    print(f"  Total Trades:       {len(trades_df):>12}")

    if not trades_df.empty:
        winners = trades_df[trades_df["pnl"] > 0]
        losers = trades_df[trades_df["pnl"] <= 0]

        win_rate = len(winners) / len(trades_df) if len(trades_df) > 0 else 0
        avg_win = winners["pnl"].mean() if len(winners) > 0 else 0
        avg_loss = losers["pnl"].mean() if len(losers) > 0 else 0
        profit_factor = (
            abs(winners["pnl"].sum() / losers["pnl"].sum())
            if losers["pnl"].sum() != 0
            else 0
        )

        print(f"  Win Rate:           {win_rate:>12.1%}")
        print(f"  Avg Win:            ${avg_win:>11,.2f}")
        print(f"  Avg Loss:           ${avg_loss:>11,.2f}")
        print(f"  Profit Factor:      {profit_factor:>12.2f}")
        print(f"  Avg Hold Days:      {trades_df['hold_days'].mean():>12.1f}")

        print("\nExit Reasons:")
        for reason, count in trades_df["exit_reason"].value_counts().items():
            print(f"    {reason}: {count}")

    # Risk commentary
    print("\nRisk Commentary:")
    print(f"  Target Vol:         {cfg.TARGET_ANNUAL_VOL:>12.1%}")
    print(f"  Realized Vol:       {annual_vol:>12.1%}")
    vol_ratio = annual_vol / cfg.TARGET_ANNUAL_VOL if cfg.TARGET_ANNUAL_VOL > 0 else 1
    if vol_ratio > 1.2:
        print(f"  ⚠️  Vol running {(vol_ratio-1)*100:.0f}% above target")
    elif vol_ratio < 0.8:
        print(f"  ℹ️  Vol running {(1-vol_ratio)*100:.0f}% below target")
    else:
        print("  ✓  Vol within acceptable range")
    if max_dd < -cfg.MAX_DRAWDOWN_SOFT:
        print(f"  ⚠️  Max DD exceeded soft limit ({cfg.MAX_DRAWDOWN_SOFT:.0%})")
    if max_dd < -cfg.MAX_DRAWDOWN_HARD:
        print(f"  🛑 Max DD exceeded hard limit ({cfg.MAX_DRAWDOWN_HARD:.0%})")

    return {
        "start_equity": start_equity,
        "final_equity": final_equity,
        "total_return": total_return,
        "cagr": cagr,
        "annual_vol": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "turnover": turnover,
        "beta": beta,
        "correlation": corr,
        "num_trades": len(trades_df),
        "win_rate": (
            len(trades_df[trades_df["pnl"] > 0]) / len(trades_df)
            if len(trades_df) > 0
            else 0
        ),
    }


# ============================================================
# MAIN ENTRY POINT
# ============================================================


def main():
    """Standalone execution for testing."""
    signals = prepare_data()
    equity_df, trades_df = backtest(signals)
    stats = compute_stats(equity_df, trades_df)
    return equity_df, trades_df, stats


if __name__ == "__main__":
    main()

"""
Sleeve Trend v1 - Technical Indicators (Revision B)
Pure functions for indicator calculations.

No changes from Draft A except added realized_volatility() for vol targeting.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """
    Average Directional Index with +DI and -DI.
    ADX > 20 = trending, ADX > 30 = strong trend.
    """
    high_diff = high.diff()
    low_diff = -low.diff()
    
    plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
    
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)
    
    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Smooth
    atr_smooth = true_range.ewm(span=period, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(span=period, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(span=period, adjust=False).mean()
    
    # +DI and -DI
    plus_di = (plus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100
    minus_di = (minus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100
    
    # DX and ADX
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = (di_diff / di_sum.replace(0, np.nan)) * 100
    adx_val = dx.ewm(span=period, adjust=False).mean()
    
    return pd.DataFrame({
        'adx': adx_val,
        'plus_di': plus_di,
        'minus_di': minus_di
    })


def crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True when fast crosses above slow."""
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def crossunder(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True when fast crosses below slow."""
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


def realized_volatility(returns: pd.Series, lookback: int = 20, annualize: bool = True) -> pd.Series:
    """
    Calculate rolling realized volatility.
    
    Args:
        returns: Daily return series
        lookback: Rolling window (default 20 days)
        annualize: If True, annualize by sqrt(252)
    
    Returns:
        Rolling volatility series
    """
    vol = returns.rolling(window=lookback).std()
    if annualize:
        vol = vol * np.sqrt(252)
    return vol


def compute_trend_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all trend indicators for a single ticker's OHLCV data.
    
    Args:
        df: DataFrame with columns: date, open, high, low, close, volume
    
    Returns:
        DataFrame with indicator columns added
    """
    from . import config as cfg
    
    result = df.copy()
    
    # Moving averages
    result['ema_fast'] = ema(result['close'], cfg.EMA_FAST)
    result['ema_slow'] = ema(result['close'], cfg.EMA_SLOW)
    result['ema_trend'] = ema(result['close'], cfg.EMA_TREND_FILTER)
    
    # ATR
    result['atr'] = atr(result['high'], result['low'], result['close'], cfg.ATR_PERIOD)
    
    # ADX
    adx_df = adx(result['high'], result['low'], result['close'], cfg.ADX_PERIOD)
    result['adx'] = adx_df['adx']
    result['plus_di'] = adx_df['plus_di']
    result['minus_di'] = adx_df['minus_di']
    
    # Volume
    result['volume_sma'] = sma(result['volume'], cfg.VOLUME_LOOKBACK)
    result['volume_ratio'] = result['volume'] / result['volume_sma']
    
    # Crossover signals (detected at CLOSE of day t)
    result['golden_cross'] = crossover(result['ema_fast'], result['ema_slow'])
    result['death_cross'] = crossunder(result['ema_fast'], result['ema_slow'])
    
    # Trend direction
    result['above_trend'] = result['close'] > result['ema_trend']
    result['below_trend'] = result['close'] < result['ema_trend']
    
    # Daily returns for vol calculation
    result['daily_return'] = result['close'].pct_change()
    
    return result

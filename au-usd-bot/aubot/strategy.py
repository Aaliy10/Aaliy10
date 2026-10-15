"""EMA-crossover trend strategy with a long-term trend filter and RSI guard.

Long entry:  fast EMA crosses above slow EMA, close is above the trend EMA,
             and RSI is not overbought.
Short entry: fast EMA crosses below slow EMA, close is below the trend EMA,
             and RSI is not oversold.
Exit:        the opposite crossover, or the ATR-based stop-loss / take-profit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams
from .indicators import atr, ema, rsi


def compute_signals(candles: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """Return the candles with indicator columns plus `entry`, `exit_long`, `exit_short`.

    Signals are evaluated on each bar's close; act on them at the next bar.
    """
    df = candles.copy()
    close = df["close"]
    df["ema_fast"] = ema(close, params.fast_ema)
    df["ema_slow"] = ema(close, params.slow_ema)
    df["ema_trend"] = ema(close, params.trend_ema)
    df["rsi"] = rsi(close, params.rsi_period)
    df["atr"] = atr(df["high"], df["low"], close, params.atr_period)

    gap = df["ema_fast"] - df["ema_slow"]
    prev_gap = gap.shift(1)
    cross_up = (gap > 0) & (prev_gap <= 0)
    cross_down = (gap < 0) & (prev_gap >= 0)

    long_ok = (close > df["ema_trend"]) & (df["rsi"] < params.rsi_overbought)
    short_ok = (close < df["ema_trend"]) & (df["rsi"] > params.rsi_oversold)

    df["entry"] = np.select([cross_up & long_ok, cross_down & short_ok], [1, -1], 0)
    df["exit_long"] = cross_down
    df["exit_short"] = cross_up
    return df


def bracket(direction: int, entry_price: float, atr_value: float,
            params: StrategyParams) -> tuple[float, float]:
    """Stop-loss and take-profit prices for a trade in `direction` (+1 long, -1 short)."""
    stop = entry_price - direction * params.stop_atr * atr_value
    target = entry_price + direction * params.target_atr * atr_value
    return stop, target

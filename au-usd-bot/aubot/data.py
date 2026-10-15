"""Loading candles from CSV and generating synthetic candles for offline testing."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .config import Instrument

PRICE_COLUMNS = ["open", "high", "low", "close"]


def load_csv(path: str | os.PathLike) -> pd.DataFrame:
    """Load OHLC candles from a CSV.

    Accepts a `time`/`datetime`/`date`/`timestamp` column (or separate `date` and
    `time` columns, as in MetaTrader exports) plus open/high/low/close and an
    optional volume. Column names are case-insensitive; `<OPEN>`-style names work.
    """
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [str(c).strip().strip("<>").lower() for c in df.columns]

    if "date" in df.columns and "time" in df.columns:
        stamps = df["date"].astype(str) + " " + df["time"].astype(str)
    else:
        time_col = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
        if time_col is None:
            raise ValueError(f"{path}: no time/datetime/date/timestamp column found")
        stamps = df[time_col]

    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    columns = PRICE_COLUMNS + (["volume"] if "volume" in df.columns else [])
    out = df[columns].astype(float)
    out.index = pd.DatetimeIndex(pd.to_datetime(stamps, utc=True), name="time")
    out = out.dropna(subset=PRICE_COLUMNS)
    return out[~out.index.duplicated(keep="last")].sort_index()


def save_csv(candles: pd.DataFrame, path: str | os.PathLike) -> None:
    candles.to_csv(path, index_label="time")


def synthetic_candles(instrument: Instrument, bars: int = 6000, seed: int = 7,
                      start: str = "2024-01-01") -> pd.DataFrame:
    """Hourly random-walk candles with alternating trend regimes.

    Useful for checking that the bot runs end to end. Results on synthetic data
    say nothing about how the strategy performs on the real market.
    """
    rng = np.random.default_rng(seed)
    sigma = instrument.annual_vol / np.sqrt(260 * 24)

    drift = np.empty(bars)
    i = 0
    while i < bars:
        length = int(rng.integers(150, 600))
        drift[i:i + length] = rng.choice([-1, 1]) * rng.uniform(0.5, 1.5) * 0.025 * sigma
        i += length

    log_returns = drift + rng.normal(0, sigma, bars)
    close = instrument.start_price * np.exp(np.cumsum(log_returns))
    open_ = np.concatenate([[instrument.start_price], close[:-1]])
    wick = np.abs(rng.normal(0, sigma / 2, (2, bars)))
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])

    index = pd.date_range(start=start, periods=bars, freq="h", tz="UTC", name="time")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(100, 5000, bars).astype(float),
    }, index=index)

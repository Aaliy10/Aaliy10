"""Instrument presets, strategy/risk parameters and .env loading."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Instrument:
    symbol: str         # OANDA instrument name
    label: str
    pip: float          # size of one pip in price units
    precision: int      # decimals the broker accepts for prices
    spread: float       # typical spread in price units, used by the backtester
    start_price: float  # seed price for synthetic data
    annual_vol: float   # volatility for synthetic data


INSTRUMENTS = {
    "AUD_USD": Instrument("AUD_USD", "AUD/USD", 0.0001, 5, 0.00012, 0.66, 0.08),
    "XAU_USD": Instrument("XAU_USD", "XAU/USD (gold)", 0.01, 3, 0.40, 4000.0, 0.16),
}

_ALIASES = {
    "AUDUSD": "AUD_USD",
    "AUD": "AUD_USD",
    "XAUUSD": "XAU_USD",
    "XAU": "XAU_USD",
    "GOLD": "XAU_USD",
}


def get_instrument(symbol: str) -> Instrument:
    key = symbol.strip().upper().replace("/", "_")
    key = _ALIASES.get(key.replace("_", ""), key)
    if key not in INSTRUMENTS:
        choices = ", ".join(INSTRUMENTS)
        raise ValueError(f"Unknown instrument {symbol!r}; choose one of: {choices}")
    return INSTRUMENTS[key]


@dataclass(frozen=True)
class StrategyParams:
    fast_ema: int = 20
    slow_ema: int = 50
    trend_ema: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    stop_atr: float = 2.0    # stop-loss distance in ATRs
    target_atr: float = 3.0  # take-profit distance in ATRs

    def __post_init__(self) -> None:
        if not 0 < self.fast_ema < self.slow_ema:
            raise ValueError("fast_ema must be positive and smaller than slow_ema")
        if self.stop_atr <= 0 or self.target_atr <= 0:
            raise ValueError("stop_atr and target_atr must be positive")


@dataclass(frozen=True)
class RiskParams:
    risk_per_trade: float = 0.01  # fraction of equity lost if the stop is hit
    max_leverage: float = 10.0    # cap on position notional / equity
    max_daily_loss: float = 0.03  # live bot stops opening trades after this daily drawdown

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade <= 0.1:
            raise ValueError("risk_per_trade must be between 0 and 0.1 (10%)")
        if self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing variables."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

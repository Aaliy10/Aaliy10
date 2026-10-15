"""Position sizing.

Both supported instruments are quoted in USD, so with a USD-denominated account
the P&L of one unit is simply the price change. Sizing assumes a USD account.
"""
from __future__ import annotations

import math

from .config import RiskParams


def position_size(equity: float, entry_price: float, stop_price: float,
                  risk: RiskParams) -> int:
    """Units to trade so that hitting the stop loses `risk.risk_per_trade` of equity."""
    stop_distance = abs(entry_price - stop_price)
    if equity <= 0 or entry_price <= 0 or stop_distance <= 0:
        return 0
    units = equity * risk.risk_per_trade / stop_distance
    max_units = equity * risk.max_leverage / entry_price
    # round away float noise (e.g. 19999.9999...) before flooring to whole units
    return int(math.floor(round(min(units, max_units), 6)))

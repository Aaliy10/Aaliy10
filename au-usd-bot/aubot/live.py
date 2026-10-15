"""Live / paper trading loop.

Every poll the bot fetches recent candles. When a new candle has closed it
evaluates the strategy on it, closes positions on an opposite crossover, and
opens new positions with a stop-loss and take-profit attached on the broker's
side, so they stay protected even if the bot stops running.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

import pandas as pd

from .config import Instrument, RiskParams, StrategyParams
from .risk import position_size
from .strategy import bracket, compute_signals

log = logging.getLogger("aubot")


class Broker(Protocol):
    def candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame: ...
    def nav(self) -> float: ...
    def position(self, instrument: str) -> int: ...
    def market_order(self, instrument: str, units: int, stop_loss: float,
                     take_profit: float, precision: int) -> dict: ...
    def close_position(self, instrument: str, direction: int) -> dict: ...


class TradingBot:
    def __init__(self, broker: Broker, instrument: Instrument,
                 strategy: StrategyParams | None = None, risk: RiskParams | None = None,
                 granularity: str = "H1", dry_run: bool = False):
        self.broker = broker
        self.instrument = instrument
        self.strategy = strategy or StrategyParams()
        self.risk = risk or RiskParams()
        self.granularity = granularity
        self.dry_run = dry_run
        self.lookback = max(500, self.strategy.trend_ema * 3)
        self.last_bar: pd.Timestamp | None = None
        self.day = None
        self.day_start_nav: float | None = None

    def step(self) -> list[str]:
        """Run one poll. Returns the actions taken (empty if none)."""
        candles = self.broker.candles(self.instrument.symbol, self.granularity, self.lookback)
        if "complete" in candles:
            candles = candles[candles["complete"]]
        if len(candles) < self.strategy.trend_ema + 2:
            log.warning("Only %d completed candles; need at least %d",
                        len(candles), self.strategy.trend_ema + 2)
            return []

        bar_time = candles.index[-1]
        if bar_time == self.last_bar:
            return []
        primed = self.last_bar is not None
        self.last_bar = bar_time
        if not primed:
            log.info("Primed on %s %s candle at %s; acting from the next close",
                     self.instrument.label, self.granularity, bar_time)
            return []

        bar = compute_signals(candles, self.strategy).iloc[-1]
        nav = self.broker.nav()
        halted = self._daily_loss_hit(nav, bar_time)
        position = self.broker.position(self.instrument.symbol)
        log.info("Bar %s close=%.*f rsi=%.1f atr=%.*f nav=%.2f position=%d",
                 bar_time, self.instrument.precision, bar["close"], bar["rsi"],
                 self.instrument.precision, bar["atr"], nav, position)

        actions: list[str] = []
        if (position > 0 and bar["exit_long"]) or (position < 0 and bar["exit_short"]):
            actions.append(self._close(position))
            position = 0

        if position == 0 and bar["entry"] != 0:
            if halted:
                log.warning("Daily loss limit reached; skipping new entry")
            else:
                action = self._enter(int(bar["entry"]), float(bar["close"]), float(bar["atr"]), nav)
                if action:
                    actions.append(action)
        return actions

    def _daily_loss_hit(self, nav: float, now: pd.Timestamp) -> bool:
        if now.date() != self.day:
            self.day, self.day_start_nav = now.date(), nav
        return nav <= self.day_start_nav * (1 - self.risk.max_daily_loss)

    def _close(self, position: int) -> str:
        side = "long" if position > 0 else "short"
        log.info("%sClosing %s %d units (opposite crossover)", self._prefix(), side, abs(position))
        if not self.dry_run:
            self.broker.close_position(self.instrument.symbol, position)
        return f"close_{side}"

    def _enter(self, direction: int, price: float, atr_value: float, nav: float) -> str | None:
        stop, target = bracket(direction, price, atr_value, self.strategy)
        units = position_size(nav, price, stop, self.risk)
        if units <= 0:
            log.warning("Position size is 0 (nav=%.2f); skipping entry", nav)
            return None
        side = "long" if direction > 0 else "short"
        p = self.instrument.precision
        log.info("%sOpening %s %d units near %.*f  SL %.*f  TP %.*f", self._prefix(), side,
                 units, p, price, p, stop, p, target)
        if not self.dry_run:
            self.broker.market_order(self.instrument.symbol, direction * units, stop, target, p)
        return f"enter_{side}"

    def _prefix(self) -> str:
        return "[dry-run] " if self.dry_run else ""

    def run(self, poll_seconds: float = 30.0, sleep: Callable[[float], None] = time.sleep) -> None:
        log.info("Trading %s on %s candles (%s)", self.instrument.label, self.granularity,
                 "dry run, no orders" if self.dry_run else "placing orders")
        while True:
            try:
                self.step()
            except Exception:  # keep the loop alive through network/API hiccups
                log.exception("Step failed; retrying in %.0fs", poll_seconds)
            sleep(poll_seconds)

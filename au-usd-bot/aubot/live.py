"""Live / paper trading loop.

Every poll the bot fetches recent candles. When a new candle has closed it
evaluates the strategy on it, closes positions on an opposite crossover, and
opens new positions with a stop-loss and take-profit attached on the broker's
side, so they stay protected even if the bot stops running. Between candles it
watches any open position so it can report when the broker closes it.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

import pandas as pd

from .config import Instrument, RiskParams, StrategyParams
from .notify import NullNotifier
from .risk import position_size
from .strategy import bracket, compute_signals

log = logging.getLogger("aubot")

ALERT_AFTER_FAILURES = 3  # consecutive failed polls before a phone alert


class Broker(Protocol):
    def candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame: ...
    def nav(self) -> float: ...
    def position(self, instrument: str) -> int: ...
    def market_order(self, instrument: str, units: int, stop_loss: float,
                     take_profit: float, precision: int) -> dict: ...
    def close_position(self, instrument: str, direction: int) -> dict: ...
    def last_closed_trade(self, instrument: str) -> dict | None: ...


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...


class TradingBot:
    def __init__(self, broker: Broker, instrument: Instrument,
                 strategy: StrategyParams | None = None, risk: RiskParams | None = None,
                 granularity: str = "H1", dry_run: bool = False,
                 notifier: Notifier | None = None):
        self.broker = broker
        self.instrument = instrument
        self.strategy = strategy or StrategyParams()
        self.risk = risk or RiskParams()
        self.granularity = granularity
        self.dry_run = dry_run
        self.notifier = notifier or NullNotifier()
        self.lookback = max(500, self.strategy.trend_ema * 3)
        self.last_bar: pd.Timestamp | None = None
        self.day = None
        self.day_start_nav: float | None = None
        self.halt_alerted_day = None
        self.position_seen = 0  # last known broker position, to spot closes by SL/TP

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
            if self.position_seen:
                self._track_position(self.broker.position(self.instrument.symbol))
            return []
        primed = self.last_bar is not None
        self.last_bar = bar_time
        if not primed:
            self.position_seen = self.broker.position(self.instrument.symbol)
            log.info("Primed on %s %s candle at %s; acting from the next close",
                     self.instrument.label, self.granularity, bar_time)
            return []

        bar = compute_signals(candles, self.strategy).iloc[-1]
        nav = self.broker.nav()
        halted = self._daily_loss_hit(nav, bar_time)
        position = self.broker.position(self.instrument.symbol)
        self._track_position(position)
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
                self._alert_halt(nav, bar_time)
            else:
                action = self._enter(int(bar["entry"]), float(bar["close"]), float(bar["atr"]), nav)
                if action:
                    actions.append(action)
        return actions

    def _daily_loss_hit(self, nav: float, now: pd.Timestamp) -> bool:
        if now.date() != self.day:
            self.day, self.day_start_nav = now.date(), nav
        return nav <= self.day_start_nav * (1 - self.risk.max_daily_loss)

    def _track_position(self, position: int) -> None:
        """Alert if a position we knew about was closed outside the bot (SL/TP or by hand)."""
        if self.position_seen and position == 0:
            side = "long" if self.position_seen > 0 else "short"
            log.info("%s %s closed on the broker side", self.instrument.label, side)
            self._alert_closed(f"{self.instrument.label} {side} closed (stop-loss, take-profit or manual)")
        self.position_seen = position

    def _close(self, position: int) -> str:
        side = "long" if position > 0 else "short"
        log.info("%sClosing %s %d units (opposite crossover)", self._prefix(), side, abs(position))
        if not self.dry_run:
            try:
                self.broker.close_position(self.instrument.symbol, position)
            except Exception as exc:
                self.notifier.send(f"⚠️ Could not close {self.instrument.label} {side}: {exc}\n"
                                   "Its stop-loss and take-profit are still active. Check OANDA.")
                raise
            self.position_seen = 0
            self._alert_closed(f"Closed {self.instrument.label} {side} (opposite crossover)")
        else:
            self.notifier.send(f"[dry run] Would close {self.instrument.label} {side} "
                               "(opposite crossover)")
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
            try:
                self.broker.market_order(self.instrument.symbol, direction * units, stop, target, p)
            except Exception as exc:
                self.notifier.send(f"⚠️ {self.instrument.label} {side} order failed: {exc}")
                raise
            self.position_seen = direction * units

        risk_usd = units * abs(price - stop)
        verb = "BUY" if direction > 0 else "SELL"
        self.notifier.send(
            f"{'[dry run] Would ' if self.dry_run else ''}{'🟢' if direction > 0 else '🔴'} "
            f"{verb} {self.instrument.label}\n"
            f"{units:,} units @ ~{price:.{p}f}\n"
            f"Stop {stop:.{p}f} · Target {target:.{p}f}\n"
            f"Risk ≈ {risk_usd:,.2f} USD ({risk_usd / nav:.1%} of account)"
        )
        return f"enter_{side}"

    def _alert_halt(self, nav: float, now: pd.Timestamp) -> None:
        if self.halt_alerted_day == now.date():
            return
        self.halt_alerted_day = now.date()
        change = nav / self.day_start_nav - 1
        self.notifier.send(f"⚠️ Daily loss limit hit ({change:+.1%} today). "
                           "No new trades until tomorrow (UTC).")

    def _last_pnl(self) -> float | None:
        try:
            trade = self.broker.last_closed_trade(self.instrument.symbol)
            return float(trade["realizedPL"]) if trade else None
        except Exception:
            log.warning("Could not look up the closed trade's P&L", exc_info=True)
            return None

    def _alert_closed(self, text: str) -> None:
        pnl = self._last_pnl()
        if pnl is None:
            self.notifier.send(f"⏹ {text}")
        else:
            log.info("Realized P&L %+.2f USD", pnl)
            self.notifier.send(f"{'✅' if pnl >= 0 else '❌'} {text}\nP&L: {pnl:+,.2f} USD")

    def _prefix(self) -> str:
        return "[dry-run] " if self.dry_run else ""

    def run(self, poll_seconds: float = 30.0, sleep: Callable[[float], None] = time.sleep) -> None:
        log.info("Trading %s on %s candles (%s)", self.instrument.label, self.granularity,
                 "dry run, no orders" if self.dry_run else "placing orders")
        failures = 0
        while True:
            try:
                self.step()
                if failures >= ALERT_AFTER_FAILURES:
                    self.notifier.send("✅ aubot is working again")
                failures = 0
            except Exception as exc:  # keep the loop alive through network/API hiccups
                failures += 1
                log.exception("Step failed; retrying in %.0fs", poll_seconds)
                if failures == ALERT_AFTER_FAILURES:
                    self.notifier.send(f"⚠️ aubot has failed {failures} times in a row: {exc}\n"
                                       f"Still retrying every {poll_seconds:.0f}s.")
            sleep(poll_seconds)

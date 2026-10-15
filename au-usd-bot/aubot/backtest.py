"""Bar-by-bar backtester.

Rules, chosen to avoid flattering results:
* Signals from bar i's close are acted on at bar i+1's open.
* Buys fill at the ask (mid + half spread), sells at the bid (mid - half spread).
* If a bar opens beyond the stop or target the fill is at the open (gap).
* If a bar touches both the stop and the target, the stop is assumed to hit first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Instrument, RiskParams, StrategyParams
from .risk import position_size
from .strategy import bracket, compute_signals


@dataclass
class Trade:
    direction: int  # +1 long, -1 short
    units: int
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return self.direction * self.units * (self.exit_price - self.entry_price)

    def close(self, time: pd.Timestamp, price: float, reason: str) -> float:
        self.exit_time, self.exit_price, self.exit_reason = time, price, reason
        return self.pnl


def check_bracket(trade: Trade, open_: float, high: float, low: float,
                  half_spread: float) -> tuple[float, str] | None:
    """Return (fill price, reason) if this bar hits the trade's stop or target."""
    if trade.direction == 1:  # a long is closed by selling at the bid
        o, h, l = open_ - half_spread, high - half_spread, low - half_spread
        if o <= trade.stop:
            return o, "stop"
        if o >= trade.target:
            return o, "target"
        if l <= trade.stop:
            return trade.stop, "stop"
        if h >= trade.target:
            return trade.target, "target"
    else:  # a short is closed by buying at the ask
        o, h, l = open_ + half_spread, high + half_spread, low + half_spread
        if o >= trade.stop:
            return o, "stop"
        if o <= trade.target:
            return o, "target"
        if h >= trade.stop:
            return trade.stop, "stop"
        if l <= trade.target:
            return trade.target, "target"
    return None


@dataclass
class BacktestResult:
    instrument: Instrument
    initial_equity: float
    trades: list[Trade]
    equity: pd.Series
    stats: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        rows = [{
            "direction": "long" if t.direction == 1 else "short",
            "units": t.units,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "stop": t.stop,
            "target": t.target,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "pnl": t.pnl,
        } for t in self.trades]
        return pd.DataFrame(rows)

    def summary(self) -> str:
        s = self.stats
        start, end = self.equity.index[0], self.equity.index[-1]
        lines = [
            f"{self.instrument.label} backtest  {start:%Y-%m-%d} -> {end:%Y-%m-%d}  ({len(self.equity)} bars)",
            f"  Start equity    {self.initial_equity:>12,.2f}",
            f"  End equity      {s['final_equity']:>12,.2f}",
            f"  Total return    {s['total_return']:>12.2%}",
            f"  Max drawdown    {s['max_drawdown']:>12.2%}",
            f"  Sharpe (ann.)   {s['sharpe']:>12.2f}",
            f"  Trades          {s['trades']:>12d}",
            f"  Win rate        {s['win_rate']:>12.1%}",
            f"  Profit factor   {s['profit_factor']:>12.2f}",
            f"  Avg trade P&L   {s['avg_trade']:>12,.2f}",
        ]
        return "\n".join(lines)


def compute_stats(trades: list[Trade], equity: pd.Series, initial_equity: float) -> dict:
    pnls = np.array([t.pnl for t in trades], dtype=float)
    gross_win = pnls[pnls > 0].sum()
    gross_loss = -pnls[pnls < 0].sum()
    final = float(equity.iloc[-1])
    drawdown = float((equity / equity.cummax() - 1).min())

    returns = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400)
    bars_per_year = len(equity) / years if years > 0 else 0.0
    std = float(returns.std()) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean()) / std * math.sqrt(bars_per_year) if std > 0 else 0.0

    return {
        "final_equity": final,
        "total_return": final / initial_equity - 1,
        "max_drawdown": drawdown,
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": float((pnls > 0).mean()) if len(pnls) else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "avg_trade": float(pnls.mean()) if len(pnls) else 0.0,
    }


def run_backtest(candles: pd.DataFrame, instrument: Instrument,
                 strategy: StrategyParams | None = None, risk: RiskParams | None = None,
                 initial_equity: float = 10_000.0, spread: float | None = None) -> BacktestResult:
    strategy = strategy or StrategyParams()
    risk = risk or RiskParams()
    half = (instrument.spread if spread is None else spread) / 2
    if len(candles) <= strategy.trend_ema:
        raise ValueError(f"Need more than {strategy.trend_ema} candles to backtest, got {len(candles)}")

    sig = compute_signals(candles, strategy)
    times = sig.index
    o, h, l, c = (sig[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    atr_values = sig["atr"].to_numpy(dtype=float)
    entry = sig["entry"].to_numpy()
    exit_long = sig["exit_long"].to_numpy()
    exit_short = sig["exit_short"].to_numpy()

    equity = initial_equity
    trade: Trade | None = None
    trades: list[Trade] = []
    curve = np.empty(len(sig))

    for i in range(len(sig)):
        if i > 0:
            j = i - 1  # act on the previous bar's close at this bar's open
            if trade and ((trade.direction == 1 and exit_long[j]) or
                          (trade.direction == -1 and exit_short[j])):
                equity += trade.close(times[i], o[i] - trade.direction * half, "signal")
                trades.append(trade)
                trade = None
            if trade is None and entry[j] != 0 and np.isfinite(atr_values[j]):
                direction = int(entry[j])
                price = o[i] + direction * half
                stop, target = bracket(direction, price, atr_values[j], strategy)
                units = position_size(equity, price, stop, risk)
                if units > 0:
                    trade = Trade(direction, units, times[i], price, stop, target)

        if trade:
            hit = check_bracket(trade, o[i], h[i], l[i], half)
            if hit:
                equity += trade.close(times[i], hit[0], hit[1])
                trades.append(trade)
                trade = None

        mark = equity
        if trade:
            mark += trade.direction * trade.units * (c[i] - trade.direction * half - trade.entry_price)
        curve[i] = mark

    if trade:
        equity += trade.close(times[-1], c[-1] - trade.direction * half, "end")
        trades.append(trade)
        curve[-1] = equity

    equity_curve = pd.Series(curve, index=times, name="equity")
    stats = compute_stats(trades, equity_curve, initial_equity)
    return BacktestResult(instrument, initial_equity, trades, equity_curve, stats)

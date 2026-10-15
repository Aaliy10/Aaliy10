import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from aubot.backtest import Trade, check_bracket, run_backtest
from aubot.config import RiskParams, StrategyParams, get_instrument
from aubot.data import load_csv, save_csv, synthetic_candles
from aubot.indicators import atr, ema, rsi
from aubot.risk import position_size
from aubot.strategy import bracket, compute_signals

AUD = get_instrument("AUD_USD")


class InstrumentTest(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(get_instrument("aud/usd").symbol, "AUD_USD")
        self.assertEqual(get_instrument("AUDUSD").symbol, "AUD_USD")
        self.assertEqual(get_instrument("gold").symbol, "XAU_USD")
        self.assertEqual(get_instrument("XAU/USD").symbol, "XAU_USD")
        with self.assertRaises(ValueError):
            get_instrument("EUR_USD")


class IndicatorTest(unittest.TestCase):
    def test_ema_warmup_and_constant(self):
        s = pd.Series([2.0] * 30)
        out = ema(s, 10)
        self.assertTrue(out.iloc[:9].isna().all())
        self.assertAlmostEqual(out.iloc[-1], 2.0)

    def test_rsi_extremes(self):
        self.assertAlmostEqual(rsi(pd.Series(np.arange(1.0, 40.0)), 14).iloc[-1], 100.0)
        self.assertAlmostEqual(rsi(pd.Series(np.arange(40.0, 1.0, -1)), 14).iloc[-1], 0.0)
        self.assertAlmostEqual(rsi(pd.Series([5.0] * 40), 14).iloc[-1], 50.0)

    def test_atr_constant_range(self):
        n = 50
        close = pd.Series([1.0] * n)
        out = atr(close + 0.01, close - 0.01, close, 14)
        self.assertAlmostEqual(out.iloc[-1], 0.02)

    def test_signals_have_no_lookahead(self):
        candles = synthetic_candles(AUD, bars=1500, seed=1)
        params = StrategyParams()
        full = compute_signals(candles, params)
        partial = compute_signals(candles.iloc[:900], params)
        cols = ["ema_fast", "ema_slow", "ema_trend", "rsi", "atr", "entry", "exit_long", "exit_short"]
        pd.testing.assert_frame_equal(full.iloc[:900][cols], partial[cols])


class RiskTest(unittest.TestCase):
    def test_risk_based_size(self):
        # 1% of 10,000 = 100 USD risk; 50-pip stop on AUD/USD -> 20,000 units
        self.assertEqual(position_size(10_000, 0.6600, 0.6550, RiskParams()), 20_000)

    def test_leverage_cap(self):
        # A tiny stop would imply a huge position; cap at 10x equity notional
        units = position_size(10_000, 0.6600, 0.65999, RiskParams(max_leverage=10))
        self.assertEqual(units, math.floor(100_000 / 0.66))

    def test_degenerate_inputs(self):
        self.assertEqual(position_size(10_000, 0.66, 0.66, RiskParams()), 0)
        self.assertEqual(position_size(0, 0.66, 0.65, RiskParams()), 0)

    def test_param_validation(self):
        with self.assertRaises(ValueError):
            StrategyParams(fast_ema=50, slow_ema=20)
        with self.assertRaises(ValueError):
            RiskParams(risk_per_trade=0.5)


class BracketTest(unittest.TestCase):
    def test_bracket_prices(self):
        params = StrategyParams(stop_atr=2, target_atr=3)
        self.assertEqual(bracket(1, 1.0, 0.01, params), (0.98, 1.03))
        self.assertEqual(bracket(-1, 1.0, 0.01, params), (1.02, 0.97))

    def _long(self):
        return Trade(1, 1000, pd.Timestamp("2024-01-01", tz="UTC"), 1.0, stop=0.98, target=1.03)

    def test_long_stop_and_target(self):
        t = self._long()
        self.assertEqual(check_bracket(t, 1.00, 1.01, 0.97, 0.0), (0.98, "stop"))
        self.assertEqual(check_bracket(t, 1.00, 1.04, 0.99, 0.0), (1.03, "target"))
        self.assertIsNone(check_bracket(t, 1.00, 1.02, 0.99, 0.0))

    def test_both_touched_assumes_stop(self):
        self.assertEqual(check_bracket(self._long(), 1.00, 1.05, 0.95, 0.0), (0.98, "stop"))

    def test_gap_fills_at_open(self):
        t = self._long()
        self.assertEqual(check_bracket(t, 0.96, 0.97, 0.95, 0.0), (0.96, "stop"))
        self.assertEqual(check_bracket(t, 1.05, 1.06, 1.04, 0.0), (1.05, "target"))

    def test_short_uses_ask(self):
        t = Trade(-1, 1000, pd.Timestamp("2024-01-01", tz="UTC"), 1.0, stop=1.02, target=0.97)
        # mid high 1.019 + half spread 0.002 = 1.021 ask -> stop
        self.assertEqual(check_bracket(t, 1.0, 1.019, 0.99, 0.002), (1.02, "stop"))


class BacktestTest(unittest.TestCase):
    def test_accounting_is_consistent(self):
        candles = synthetic_candles(AUD, bars=4000, seed=3)
        result = run_backtest(candles, AUD, initial_equity=10_000)
        self.assertGreater(len(result.trades), 5)
        total_pnl = sum(t.pnl for t in result.trades)
        self.assertAlmostEqual(result.stats["final_equity"], 10_000 + total_pnl, places=6)
        self.assertAlmostEqual(result.equity.iloc[-1], 10_000 + total_pnl, places=6)
        for t in result.trades:
            self.assertGreater(t.exit_time, t.entry_time - pd.Timedelta(seconds=1))
            self.assertIn(t.exit_reason, {"stop", "target", "signal", "end"})

    def test_spread_costs_money(self):
        candles = synthetic_candles(AUD, bars=4000, seed=3)
        free = run_backtest(candles, AUD, spread=0.0).stats["final_equity"]
        costly = run_backtest(candles, AUD, spread=0.0005).stats["final_equity"]
        self.assertLess(costly, free)

    def test_entries_happen_at_next_open(self):
        candles = synthetic_candles(AUD, bars=3000, seed=5)
        sig = compute_signals(candles, StrategyParams())
        result = run_backtest(candles, AUD, spread=0.0)
        for t in result.trades:
            prev = sig.index[sig.index.get_loc(t.entry_time) - 1]
            self.assertEqual(sig.at[prev, "entry"], t.direction)
            self.assertAlmostEqual(t.entry_price, candles.at[t.entry_time, "open"])


class CsvTest(unittest.TestCase):
    def test_round_trip(self):
        candles = synthetic_candles(AUD, bars=50)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.csv"
            save_csv(candles, path)
            loaded = load_csv(path)
        pd.testing.assert_frame_equal(loaded, candles, check_freq=False, check_index_type=False)

    def test_metatrader_style(self):
        text = ("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n"
                "2024.01.02\t01:00:00\t0.6800\t0.6810\t0.6790\t0.6805\t100\n"
                "2024.01.02\t00:00:00\t0.6790\t0.6802\t0.6785\t0.6800\t120\n")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mt.csv"
            path.write_text(text)
            loaded = load_csv(path)
        self.assertEqual(list(loaded.columns), ["open", "high", "low", "close"])
        self.assertEqual(loaded.index[0], pd.Timestamp("2024-01-02 00:00", tz="UTC"))
        self.assertAlmostEqual(loaded["close"].iloc[-1], 0.6805)


if __name__ == "__main__":
    unittest.main()

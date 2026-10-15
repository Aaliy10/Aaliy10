import unittest
from unittest import mock

import pandas as pd

from aubot.config import RiskParams, get_instrument
from aubot.live import TradingBot
from aubot.oanda import OandaClient, OandaError

AUD = get_instrument("AUD_USD")


class FakeBroker:
    def __init__(self, bars=300):
        index = pd.date_range("2024-03-04", periods=bars, freq="h", tz="UTC")
        self.frame = pd.DataFrame({"open": 0.66, "high": 0.661, "low": 0.659, "close": 0.66,
                                   "complete": True}, index=index)
        self.nav_value = 10_000.0
        self.pos = 0
        self.orders = []
        self.closes = []
        self.closed_trades = []
        self.order_error = None

    def new_bar(self):
        t = self.frame.index[-1] + pd.Timedelta(hours=1)
        self.frame.loc[t] = self.frame.iloc[-1]

    def candles(self, instrument, granularity, count):
        return self.frame.tail(count)

    def nav(self):
        return self.nav_value

    def position(self, instrument):
        return self.pos

    def last_closed_trade(self, instrument):
        return self.closed_trades[-1] if self.closed_trades else None

    def market_order(self, instrument, units, stop_loss, take_profit, precision):
        if self.order_error:
            raise self.order_error
        self.orders.append((instrument, units, stop_loss, take_profit, precision))
        self.pos = units
        return {}

    def close_position(self, instrument, direction):
        self.closes.append((instrument, direction))
        self.pos = 0
        return {}


def fake_signals(entry=0, exit_long=False, exit_short=False, close=0.66, atr=0.002):
    def compute(candles, params):
        df = candles.copy()
        df["rsi"] = 50.0
        df["atr"] = atr
        df["close"] = close
        df["entry"] = entry
        df["exit_long"] = exit_long
        df["exit_short"] = exit_short
        return df
    return compute


class TradingBotTest(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.bot = TradingBot(self.broker, AUD)

    def step_with(self, **signals):
        with mock.patch("aubot.live.compute_signals", fake_signals(**signals)):
            return self.bot.step()

    def test_first_step_only_primes(self):
        self.assertEqual(self.step_with(entry=1), [])
        self.assertEqual(self.broker.orders, [])

    def test_enters_long_with_bracket_on_new_bar(self):
        self.step_with()
        self.broker.new_bar()
        self.assertEqual(self.step_with(entry=1), ["enter_long"])
        symbol, units, stop, target, precision = self.broker.orders[0]
        self.assertEqual(symbol, "AUD_USD")
        self.assertEqual(units, 25_000)  # 100 USD risk / (2 * 0.002 stop)
        self.assertAlmostEqual(stop, 0.656)
        self.assertAlmostEqual(target, 0.666)
        self.assertEqual(precision, 5)

    def test_same_bar_is_not_traded_twice(self):
        self.step_with()
        self.broker.new_bar()
        self.step_with(entry=1)
        self.broker.pos = 0
        self.assertEqual(self.step_with(entry=1), [])
        self.assertEqual(len(self.broker.orders), 1)

    def test_reversal_closes_then_enters_short(self):
        self.step_with()
        self.broker.pos = 25_000
        self.broker.new_bar()
        self.assertEqual(self.step_with(entry=-1, exit_long=True), ["close_long", "enter_short"])
        self.assertEqual(self.broker.closes, [("AUD_USD", 25_000)])
        self.assertLess(self.broker.orders[0][1], 0)

    def test_no_new_entry_while_positioned(self):
        self.step_with()
        self.broker.pos = 25_000
        self.broker.new_bar()
        self.assertEqual(self.step_with(entry=1), [])

    def test_dry_run_places_nothing(self):
        bot = TradingBot(self.broker, AUD, dry_run=True)
        with mock.patch("aubot.live.compute_signals", fake_signals(entry=1)):
            bot.step()
            self.broker.new_bar()
            self.assertEqual(bot.step(), ["enter_long"])
        self.assertEqual(self.broker.orders, [])

    def test_daily_loss_limit_blocks_entries(self):
        bot = TradingBot(self.broker, AUD, risk=RiskParams(max_daily_loss=0.03))
        with mock.patch("aubot.live.compute_signals", fake_signals()):
            bot.step()
            self.broker.new_bar()
            bot.step()  # records the day's starting NAV of 10,000
        self.broker.nav_value = 9_650.0
        self.broker.new_bar()
        with mock.patch("aubot.live.compute_signals", fake_signals(entry=1)):
            self.assertEqual(bot.step(), [])
        self.assertEqual(self.broker.orders, [])

    def test_incomplete_candles_are_ignored(self):
        self.step_with()
        t = self.broker.frame.index[-1] + pd.Timedelta(hours=1)
        self.broker.frame.loc[t] = self.broker.frame.iloc[-1]
        self.broker.frame.loc[t, "complete"] = False
        self.assertEqual(self.step_with(entry=1), [])


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True


class AlertTest(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.notifier = FakeNotifier()
        self.bot = TradingBot(self.broker, AUD, notifier=self.notifier)

    def step_with(self, bot=None, **signals):
        with mock.patch("aubot.live.compute_signals", fake_signals(**signals)):
            return (bot or self.bot).step()

    def prime(self, bot=None):
        self.step_with(bot)
        self.broker.new_bar()

    def test_entry_alert(self):
        self.prime()
        self.step_with(entry=1)
        self.assertEqual(self.notifier.messages, [
            "🟢 BUY AUD/USD\n25,000 units @ ~0.66000\nStop 0.65600 · Target 0.66600\n"
            "Risk ≈ 100.00 USD (1.0% of account)"])

    def test_stop_or_target_close_is_reported_between_bars(self):
        self.prime()
        self.step_with(entry=1)
        self.broker.closed_trades.append({"realizedPL": "-100.0"})
        self.broker.pos = 0  # OANDA's stop-loss closed the trade
        self.step_with()     # same candle: only the position check runs
        self.assertEqual(self.notifier.messages[-1],
                         "❌ AUD/USD long closed (stop-loss, take-profit or manual)\nP&L: -100.00 USD")
        count = len(self.notifier.messages)
        self.step_with()
        self.assertEqual(len(self.notifier.messages), count)  # reported once

    def test_signal_close_alert_has_pnl_and_no_duplicate(self):
        self.prime()
        self.broker.pos = 25_000
        self.broker.closed_trades.append({"realizedPL": "55.5"})
        self.step_with(exit_long=True)
        self.assertEqual(self.notifier.messages,
                         ["✅ Closed AUD/USD long (opposite crossover)\nP&L: +55.50 USD"])
        self.step_with()
        self.assertEqual(len(self.notifier.messages), 1)

    def test_close_without_pnl_lookup(self):
        self.prime()
        self.broker.pos = 25_000
        self.step_with(exit_long=True)
        self.assertEqual(self.notifier.messages, ["⏹ Closed AUD/USD long (opposite crossover)"])

    def test_existing_position_at_startup_is_watched(self):
        self.broker.pos = -3_000
        self.step_with()  # primes and records the open short
        self.broker.pos = 0
        self.step_with()
        self.assertIn("AUD/USD short closed", self.notifier.messages[-1])

    def test_dry_run_alerts_say_would(self):
        bot = TradingBot(self.broker, AUD, dry_run=True, notifier=self.notifier)
        self.prime(bot)
        self.step_with(bot, entry=-1)
        self.assertTrue(self.notifier.messages[-1].startswith("[dry run] Would 🔴 SELL AUD/USD"))
        self.step_with(bot)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_order_failure_alerts_and_raises(self):
        self.prime()
        self.broker.order_error = OandaError("Order cancelled by OANDA: INSUFFICIENT_MARGIN")
        with self.assertRaises(OandaError):
            self.step_with(entry=1)
        self.assertEqual(self.notifier.messages,
                         ["⚠️ AUD/USD long order failed: Order cancelled by OANDA: INSUFFICIENT_MARGIN"])

    def test_daily_loss_alert_sent_once_per_day(self):
        self.prime()
        self.step_with()  # records the day's starting NAV of 10,000
        self.broker.nav_value = 9_650.0
        for _ in range(2):
            self.broker.new_bar()
            self.step_with(entry=1)
        self.assertEqual(self.notifier.messages,
                         ["⚠️ Daily loss limit hit (-3.5% today). No new trades until tomorrow (UTC)."])

    def test_run_loop_alerts_on_repeated_failure_and_recovery(self):
        self.bot.step = mock.Mock(side_effect=[RuntimeError("boom")] * 3 + [[], []])
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 5:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt), self.assertLogs("aubot", "ERROR"):
            self.bot.run(poll_seconds=30, sleep=sleep)
        self.assertEqual(self.notifier.messages, [
            "⚠️ aubot has failed 3 times in a row: boom\nStill retrying every 30s.",
            "✅ aubot is working again",
        ])


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, *responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class OandaClientTest(unittest.TestCase):
    def client(self, *responses):
        session = FakeSession(*responses)
        return OandaClient("tok", "001-1", session=session), session

    def test_practice_by_default_and_auth_header(self):
        client, session = self.client()
        self.assertEqual(client.base_url, "https://api-fxpractice.oanda.com")
        self.assertEqual(session.headers["Authorization"], "Bearer tok")

    def test_candles_parsing(self):
        body = {"candles": [
            {"time": "2024-01-02T00:00:00.000000000Z", "volume": 10, "complete": True,
             "mid": {"o": "0.68000", "h": "0.68100", "l": "0.67900", "c": "0.68050"}},
            {"time": "2024-01-02T01:00:00.000000000Z", "volume": 3, "complete": False,
             "mid": {"o": "0.68050", "h": "0.68060", "l": "0.68000", "c": "0.68010"}},
        ]}
        client, session = self.client(FakeResponse(body))
        df = client.candles("AUD_USD", "H1", 2)
        method, url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api-fxpractice.oanda.com/v3/instruments/AUD_USD/candles")
        self.assertEqual(kwargs["params"], {"granularity": "H1", "count": 2, "price": "M"})
        self.assertEqual(list(df["complete"]), [True, False])
        self.assertAlmostEqual(df["close"].iloc[0], 0.6805)
        self.assertEqual(df.index[0], pd.Timestamp("2024-01-02", tz="UTC"))

    def test_market_order_body(self):
        client, session = self.client(FakeResponse({"orderFillTransaction": {}}, 201))
        client.market_order("AUD_USD", -25_000, 0.664123456, 0.654, 5)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://api-fxpractice.oanda.com/v3/accounts/001-1/orders"))
        order = kwargs["json"]["order"]
        self.assertEqual(order["units"], "-25000")
        self.assertEqual(order["stopLossOnFill"]["price"], "0.66412")
        self.assertEqual(order["takeProfitOnFill"]["price"], "0.65400")

    def test_cancelled_order_raises(self):
        client, _ = self.client(FakeResponse({"orderCancelTransaction": {"reason": "INSUFFICIENT_MARGIN"}}, 201))
        with self.assertRaisesRegex(OandaError, "INSUFFICIENT_MARGIN"):
            client.market_order("AUD_USD", 1000, 0.65, 0.67, 5)

    def test_http_error_raises(self):
        client, _ = self.client(FakeResponse({"errorMessage": "Invalid token"}, 401))
        with self.assertRaisesRegex(OandaError, "Invalid token"):
            client.nav()

    def test_position_net_units(self):
        body = {"positions": [{"instrument": "XAU_USD", "long": {"units": "5"}, "short": {"units": "0"}},
                              {"instrument": "AUD_USD", "long": {"units": "0"}, "short": {"units": "-3000"}}]}
        client, _ = self.client(FakeResponse(body), FakeResponse({"positions": []}))
        self.assertEqual(client.position("AUD_USD"), -3000)
        self.assertEqual(client.position("AUD_USD"), 0)

    def test_last_closed_trade(self):
        client, session = self.client(FakeResponse({"trades": [{"id": "7", "realizedPL": "-12.5"}]}),
                                      FakeResponse({"trades": []}))
        self.assertEqual(client.last_closed_trade("XAU_USD")["realizedPL"], "-12.5")
        method, url, kwargs = session.calls[0]
        self.assertTrue(url.endswith("/v3/accounts/001-1/trades"))
        self.assertEqual(kwargs["params"], {"state": "CLOSED", "instrument": "XAU_USD", "count": 1})
        self.assertIsNone(client.last_closed_trade("XAU_USD"))

    def test_close_position(self):
        client, session = self.client(FakeResponse({}))
        client.close_position("AUD_USD", -3000)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "PUT")
        self.assertTrue(url.endswith("/v3/accounts/001-1/positions/AUD_USD/close"))
        self.assertEqual(kwargs["json"], {"shortUnits": "ALL"})


if __name__ == "__main__":
    unittest.main()

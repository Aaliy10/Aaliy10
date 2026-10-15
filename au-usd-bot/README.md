# aubot: AUD/USD trading bot

A trend-following trading bot for **AUD/USD** (Australian dollar). It also supports
**XAU/USD** (gold, chemical symbol Au) via `--instrument XAU_USD`.

It can:

- **backtest** the strategy on historical candles (or synthetic data for a quick demo)
- **fetch** historical candles from OANDA to CSV
- **run** live against an OANDA account: **practice (paper money) by default**

**Using MetaTrader 5?** The same strategy is available as an MT5 Expert Advisor
with phone push alerts. See [`mt5/README.md`](mt5/README.md).

> **Risk warning.** This is educational software, not financial advice. Leveraged
> forex/gold trading can lose more than you expect, quickly. No strategy here is
> proven profitable. Backtest on real data, then run it on a practice account for
> weeks before even considering real money.

## Strategy

Evaluated on each closed candle (default: 1-hour candles):

| | Rule |
|---|---|
| **Long entry** | 20 EMA crosses above 50 EMA, price above 200 EMA, RSI(14) < 70 |
| **Short entry** | 20 EMA crosses below 50 EMA, price below 200 EMA, RSI(14) > 30 |
| **Stop-loss** | 2 × ATR(14) from entry |
| **Take-profit** | 3 × ATR(14) from entry |
| **Exit early** | Opposite EMA crossover (reverses if the new entry rules pass) |

**Position sizing:** each trade risks 1% of account equity (the loss if the stop is
hit), capped at 10× leverage. Sizing assumes a **USD-denominated account**.

**Live safety rails:**
- Stop-loss and take-profit are attached to every order on OANDA's servers, so they
  still protect you if the bot crashes or your computer turns off.
- New entries stop for the day once the account is down 3% on the day.
- On startup the bot waits for the next candle to close before acting, so it never
  trades on a stale signal.
- Real-money trading needs both `--live` and `--i-understand-the-risks`.

All of these numbers can be changed with command-line flags (`--help` lists them).

## Setup

Requires Python 3.9+.

```bash
cd au-usd-bot
pip install -r requirements.txt
python -m unittest discover -s tests   # optional: run the test suite
```

## 1. Backtest

Quick demo on synthetic data (only checks that everything runs):

```bash
python -m aubot backtest
```

On real data, from a CSV with `time,open,high,low,close` columns. MetaTrader
exports work too:

```bash
python -m aubot backtest --csv data/aud_usd_h1.csv --trades-out trades.csv
python -m aubot backtest --csv data/aud_usd_h1.csv --fast 10 --slow 30 --stop-atr 1.5
```

The backtester acts on the *next* bar's open, charges the spread on every fill,
fills gaps at the open, and assumes the stop is hit first when a bar touches both
stop and target. On driftless random walks it loses about the spread, as an honest
backtester should.

## 2. Connect to OANDA (free practice account)

1. Create a free **demo/practice** account at [oanda.com](https://www.oanda.com).
2. In the account portal, generate a **personal API token** and note your **v20
   account ID** (looks like `101-001-1234567-001`).
3. Copy `.env.example` to `.env` and fill both in. `.env` is git-ignored, so
   never commit your token.

Download real history for backtesting:

```bash
python -m aubot fetch --instrument AUD_USD --granularity H1 --count 20000 --out data/aud_usd_h1.csv
```

## 3. Run the bot

```bash
# Watch signals without placing any orders
python -m aubot run --dry-run

# Trade the practice account (fake money)
python -m aubot run --log-file bot.log

# Gold instead of the Aussie dollar, on 4-hour candles
python -m aubot run --instrument XAU_USD --granularity H4
```

Stop it with `Ctrl+C`. Open trades keep their stop-loss and take-profit on OANDA.
To keep it running 24/5, run it on an always-on machine or a small VPS (e.g. under
`tmux`, `screen` or a systemd service).

Real money, only after long practice testing:

```bash
python -m aubot run --live --i-understand-the-risks
```

## 4. Phone alerts (Telegram)

The bot can message your phone through a Telegram bot that you create (free):

1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name. BotFather
   replies with a **token** like `123456789:AAF...`.
2. Add it to `.env` as `TELEGRAM_BOT_TOKEN=...`.
3. Open your new bot in Telegram (BotFather sends a link), tap **Start**, and send it
   any message, e.g. `hi`.
4. Run `python -m aubot telegram`. It prints a `TELEGRAM_CHAT_ID=...` line; add it
   to `.env`.
5. Run `python -m aubot telegram` again to send a test message to your phone.

From then on, `python -m aubot run` messages you when:

- the bot starts or stops
- it opens a trade (direction, size, price, stop, target and amount at risk)
- a trade closes, by opposite crossover or by OANDA's stop-loss / take-profit
  (checked every poll, so within about 30 seconds), with its profit or loss
- an order fails, the daily loss limit kicks in, or the bot keeps failing
  (e.g. lost internet) and later recovers

Alerts are skipped if Telegram is not set up, and a failed alert never stops trading.
Keep the token private: anyone with it can send messages as your bot.

## Project layout

```
aubot/
  config.py      instrument presets (AUD_USD, XAU_USD), strategy & risk parameters
  indicators.py  EMA, RSI, ATR
  strategy.py    entry/exit signals and stop/target levels
  risk.py        position sizing
  backtest.py    bar-by-bar backtester and performance stats
  data.py        CSV loading and synthetic data
  oanda.py       OANDA v20 REST client
  live.py        live trading loop
  notify.py      Telegram phone alerts
  __main__.py    command line interface
tests/           unit tests (python -m unittest discover -s tests)
mt5/             MetaTrader 5 Expert Advisor version (AuBot.mq5)
```

# aubot for MetaTrader 5

Two files, which you can use together or separately:

- **`AuBot.mq5`**, an **Expert Advisor** (EA) that trades the aubot strategy
  automatically and sends trade alerts to the **MT5 phone app**.
- **`AuPilot.mq5`**, an **indicator** that shows the strategy on your chart: the
  moving averages, arrows at every past signal, and a live panel saying what the
  strategy sees right now. It never places trades. See [AuPilot](#aupilot-signal-dashboard).

Both work on whatever symbol's chart you attach them to: **XAUUSD (gold)**,
**AUDUSD**, or your broker's name for them (`GOLD`, `XAUUSD.m`, ...).

> **Risk warning.** This can lose money. No strategy here is proven profitable.
> Backtest it, then run it on a **demo account** for weeks before even considering
> real money, and only ever risk money you can afford to lose.

## Strategy

Same rules as the Python bot, checked once per closed candle (1 hour by default):

| | Rule |
|---|---|
| **Buy** | 20 EMA crosses above 50 EMA, price above 200 EMA, RSI(14) below 70 |
| **Sell** | 20 EMA crosses below 50 EMA, price below 200 EMA, RSI(14) above 30 |
| **Stop-loss** | 2 × ATR(14) from entry, attached to the order on the broker's server |
| **Take-profit** | 3 × ATR(14) from entry, also on the broker's server |
| **Early exit** | Opposite EMA crossover (and reverses if the new entry rules pass) |
| **Size** | Loses 1% of equity if the stop is hit, capped at 10× leverage |
| **Daily limit** | No new trades for the rest of the day after a 3% daily loss |

## 1. Install

1. Install **MetaTrader 5** from your broker (or metatrader5.com) and open a
   **demo account**. MT5 runs on Windows, and on Mac through MetaQuotes' Mac installer.
2. In MT5: **File → Open Data Folder**. Copy `AuBot.mq5` into **MQL5 → Experts**,
   and `AuPilot.mq5` into **MQL5 → Indicators**.
3. Press **F4** to open MetaEditor. Open each file and press **F7** (Compile).
   The bottom panel should say `0 errors`.
4. Back in MT5, open the **Navigator** (Ctrl+N) and right-click → **Refresh**.
   `AuBot` appears under **Expert Advisors** and `AuPilot` under **Indicators**.

These files haven't been compiled yet (MT5 isn't available where they were
written). If MetaEditor shows errors, copy the error lines and they can be fixed quickly.

## 2. Backtest (Strategy Tester)

1. **View → Strategy Tester** (Ctrl+R).
2. Expert: `AuBot`. Symbol: `XAUUSD` (or `AUDUSD`). Timeframe: `H1`.
3. Modelling: **Every tick based on real ticks** (most accurate) or **1 minute OHLC** (faster).
4. Pick a date range (a few years), your deposit and leverage, then **Start**.
5. The **Backtest** tab shows profit, drawdown and win rate; **Graph** shows the equity curve.

## 3. Run it on a demo account

1. Open an **XAUUSD** (or AUDUSD) chart.
2. Drag `AuBot` from the Navigator onto the chart.
3. In the window that opens: on the **Common** tab tick **Allow Algo Trading**;
   on the **Inputs** tab change settings if you want. Click **OK**.
4. Make sure the **Algo Trading** button in the top toolbar is switched on.

The Experts tab at the bottom shows `aubot started ...`. On startup the EA waits for
the next candle to close before acting, so it never trades on an old signal.

## AuPilot: signal dashboard

Drag `AuPilot` from **Navigator → Indicators** onto an **H1** chart. It draws:

- the **20 EMA** (blue), **50 EMA** (orange) and **200 EMA** (grey, thicker)
- a **green arrow** under every candle that gave a buy signal, and a **red arrow**
  above every sell signal, so you can scroll back and see how past signals played out
- a **panel** in the top-left corner, updated every tick. For example:

```
AuPilot  XAUUSD H1
Trend    UP    price above 200 EMA (buys only)
20/50    20 above 50 for 1 candle
RSI      58.4  neutral
ATR      14.29  (stop 2.0x, target 3.0x)
Signal   BUY on the last closed candle
Plan     entry ~4174.80  stop 4146.22  target 4217.67
Size     0.03 lots risks 85.74 USD (0.9%)
Last     this one, closed at 4174.44
Rules only, not a prediction. Many signals lose.
```

(Example layout; the numbers are made up.) Most of the time the Signal line says
**none**, which is normal: crossovers that pass every filter are rare.

When a candle closes with a new signal, AuPilot sends a pop-up and a phone push alert
(both can be turned off in its **Inputs**). The arrow sits under the candle that
produced the signal; AuBot, if it's running, enters at the start of the next candle.
If you run both on the same chart you'll get two alerts per trade (AuPilot's signal
and AuBot's order), so you may want to turn off AuPilot's push alerts.

Keep AuPilot's strategy inputs the same as AuBot's, or the arrows won't match the
EA's trades.

## 4. Phone alerts

1. Install the **MetaTrader 5** app on your phone and find your **MetaQuotes ID**
   (in the app's **Settings** or **Messages** section).
2. On the computer: **Tools → Options → Notifications**, tick **Enable Push
   Notifications**, paste the MetaQuotes ID, and click **Test**.

You'll then get a push alert when:

- the EA starts or stops
- it opens a trade (size in lots, price, stop, target and amount at risk)
- a trade closes, saying whether it hit the stop-loss, hit the take-profit, closed
  on the opposite crossover or was closed by hand, with the profit or loss
- an order fails, the daily loss limit kicks in, or Algo Trading is switched off

You can also follow the trades themselves in the phone app's **Trade** and **History** tabs.

## 5. Keep it running 24/5

The EA only runs while MT5 is open on a computer that's switched on. To run it
without your computer, rent MT5's built-in **Virtual Hosting**: in the Navigator,
right-click your account → **Register a Virtual Server**, and follow the steps to
migrate the EA. It's a monthly fee shown in MT5. After migrating you should get the
"aubot started" alert from the server. Then just watch from your phone.

## Settings

| Input | Default | Meaning |
|---|---|---|
| Candle timeframe | H1 | Candles the strategy uses, regardless of the chart's timeframe |
| Fast / Slow / Trend EMA | 20 / 50 / 200 | Moving-average periods |
| RSI period, overbought, oversold | 14, 70, 30 | RSI guard for entries |
| ATR period | 14 | Volatility measure for stop and target |
| Stop-loss / Take-profit ATRs | 2 / 3 | Distance from entry in ATRs |
| Risk % | 1 | Equity lost if the stop is hit |
| Max leverage | 10 | Cap on position value ÷ equity |
| Max daily loss % | 3 | No new trades for the day after this loss |
| Push / pop-up alerts | on / off | Where alerts go |
| Magic number | 20261015 | Marks this EA's trades; use a different one per chart if you run several |
| Max slippage | 30 points | Largest price slip accepted on entry |

## Differences from the Python bot

- **Lots, not units.** Sizes are in lots (usually 1 lot = 100 oz of gold or
  100,000 AUD; the minimum is often 0.01 lot). Sizing uses the broker's own contract
  values, so it works with any account currency. If 1% risk is less than the
  broker's minimum lot, the trade is skipped (with an alert) instead of risking more.
- **ATR.** MT5's built-in ATR is a simple average of the true range; the Python bot
  uses Wilder's smoothing. Stops and targets differ slightly as a result.
- **Daily loss limit** resets on the broker's server day, not UTC midnight.

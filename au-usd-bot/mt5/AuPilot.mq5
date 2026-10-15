//+------------------------------------------------------------------+
//| AuPilot.mq5                                                      |
//| On-chart signal dashboard for the aubot strategy.                |
//|                                                                  |
//| Draws the fast/slow/trend EMAs, marks every buy/sell signal with |
//| an arrow, shows a live panel of what the strategy sees right now |
//| (trend, crossover, RSI, ATR, signal, entry/stop/target and lot   |
//| size) and alerts on each new signal. It never places trades;     |
//| the AuBot Expert Advisor does that.                              |
//+------------------------------------------------------------------+
#property copyright   "aubot"
#property version     "1.00"
#property description "Signal dashboard for the aubot strategy: EMAs, signal arrows, live panel and alerts. Never places trades."
#property indicator_chart_window
#property indicator_buffers 5
#property indicator_plots   5

#property indicator_label1 "Fast EMA"
#property indicator_type1  DRAW_LINE
#property indicator_color1 clrDodgerBlue
#property indicator_width1 1
#property indicator_label2 "Slow EMA"
#property indicator_type2  DRAW_LINE
#property indicator_color2 clrOrange
#property indicator_width2 1
#property indicator_label3 "Trend EMA"
#property indicator_type3  DRAW_LINE
#property indicator_color3 clrSilver
#property indicator_width3 2
#property indicator_label4 "Buy signal"
#property indicator_type4  DRAW_ARROW
#property indicator_color4 clrLimeGreen
#property indicator_width4 2
#property indicator_label5 "Sell signal"
#property indicator_type5  DRAW_ARROW
#property indicator_color5 clrTomato
#property indicator_width5 2

#define PREFIX  "AuPilot_"
#define PANEL_X 10
#define PANEL_Y 25
#define PANEL_W 400
#define ROW_H   17
#define ROWS    11

input group "Strategy (keep the same as AuBot)"
input int    InpFastEMA       = 20;   // Fast EMA period
input int    InpSlowEMA       = 50;   // Slow EMA period
input int    InpTrendEMA      = 200;  // Trend-filter EMA period
input int    InpRSIPeriod     = 14;   // RSI period
input double InpRSIOverbought = 70;   // No new buys at or above this RSI
input double InpRSIOversold   = 30;   // No new sells at or below this RSI
input int    InpATRPeriod     = 14;   // ATR period
input double InpStopATR       = 2.0;  // Stop-loss distance in ATRs
input double InpTargetATR     = 3.0;  // Take-profit distance in ATRs

input group "Position size shown in the panel"
input double InpRiskPct     = 1.0;  // % of equity lost if the stop-loss is hit
input double InpMaxLeverage = 10.0; // Max position value / equity

input group "Alerts on new signals"
input bool InpPushAlerts  = true; // Push alerts to the MT5 phone app
input bool InpPopupAlerts = true; // Pop-up alerts on the computer

double FastBuf[], SlowBuf[], TrendBuf[], BuyBuf[], SellBuf[];
int    hFast  = INVALID_HANDLE;
int    hSlow  = INVALID_HANDLE;
int    hTrend = INVALID_HANDLE;
int    hRSI   = INVALID_HANDLE;
int    hATR   = INVALID_HANDLE;
datetime lastAlertBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpFastEMA <= 0 || InpFastEMA >= InpSlowEMA)
   {
      Print("Fast EMA must be positive and smaller than the slow EMA");
      return INIT_PARAMETERS_INCORRECT;
   }

   SetIndexBuffer(0, FastBuf, INDICATOR_DATA);
   SetIndexBuffer(1, SlowBuf, INDICATOR_DATA);
   SetIndexBuffer(2, TrendBuf, INDICATOR_DATA);
   SetIndexBuffer(3, BuyBuf, INDICATOR_DATA);
   SetIndexBuffer(4, SellBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "EMA " + IntegerToString(InpFastEMA));
   PlotIndexSetString(1, PLOT_LABEL, "EMA " + IntegerToString(InpSlowEMA));
   PlotIndexSetString(2, PLOT_LABEL, "EMA " + IntegerToString(InpTrendEMA));
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpFastEMA);
   PlotIndexSetInteger(1, PLOT_DRAW_BEGIN, InpSlowEMA);
   PlotIndexSetInteger(2, PLOT_DRAW_BEGIN, InpTrendEMA);
   PlotIndexSetInteger(3, PLOT_ARROW, 233);       // Wingdings up arrow
   PlotIndexSetInteger(4, PLOT_ARROW, 234);       // Wingdings down arrow
   PlotIndexSetInteger(3, PLOT_ARROW_SHIFT, 12);  // pixels below the candle's low
   PlotIndexSetInteger(4, PLOT_ARROW_SHIFT, -12); // pixels above the candle's high
   PlotIndexSetDouble(3, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(4, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, "AuPilot");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   hFast  = iMA(_Symbol, _Period, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, _Period, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hTrend = iMA(_Symbol, _Period, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI   = iRSI(_Symbol, _Period, InpRSIPeriod, PRICE_CLOSE);
   hATR   = iATR(_Symbol, _Period, InpATRPeriod);
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hTrend == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE)
   {
      Print("Could not create indicators, error ", GetLastError());
      return INIT_FAILED;
   }
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, PREFIX);
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   IndicatorRelease(hTrend);
   IndicatorRelease(hRSI);
   IndicatorRelease(hATR);
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[],
                const double &close[], const long &tick_volume[], const long &volume[],
                const int &spread[])
{
   if(rates_total < InpTrendEMA + 3)
      return 0;
   if(BarsCalculated(hFast) < rates_total || BarsCalculated(hSlow) < rates_total ||
      BarsCalculated(hTrend) < rates_total || BarsCalculated(hRSI) < rates_total ||
      BarsCalculated(hATR) < rates_total)
      return 0; // the built-in indicators are still calculating

   // recalculate from two candles back so a newly closed candle is always included
   int start = prev_calculated > 2 ? prev_calculated - 2 : 0;
   int count = rates_total - start;
   double f[], s[], t[], r[], a[]; // oldest first, f[k] belongs to candle start + k
   if(CopyBuffer(hFast, 0, 0, count, f) != count || CopyBuffer(hSlow, 0, 0, count, s) != count ||
      CopyBuffer(hTrend, 0, 0, count, t) != count || CopyBuffer(hRSI, 0, 0, count, r) != count ||
      CopyBuffer(hATR, 0, 0, count, a) != count)
      return 0;

   for(int k = 0; k < count; k++)
   {
      int i = start + k;
      FastBuf[i]  = f[k];
      SlowBuf[i]  = s[k];
      TrendBuf[i] = t[k];
      BuyBuf[i]   = EMPTY_VALUE;
      SellBuf[i]  = EMPTY_VALUE;
      if(i < InpTrendEMA || i >= rates_total - 1)
         continue; // warm-up, or the candle that is still forming

      double gapNow  = f[k] - s[k];
      double gapPrev = FastBuf[i - 1] - SlowBuf[i - 1];
      if(gapNow > 0 && gapPrev <= 0 && close[i] > t[k] && r[k] < InpRSIOverbought)
         BuyBuf[i] = low[i];
      else if(gapNow < 0 && gapPrev >= 0 && close[i] < t[k] && r[k] > InpRSIOversold)
         SellBuf[i] = high[i];
   }

   int last = rates_total - 2; // last closed candle
   double rsi = r[last - start];
   double atr = a[last - start];
   UpdatePanel(last, close, rsi, atr);
   CheckNewSignal(time[rates_total - 1], last, atr);
   return rates_total;
}

//+------------------------------------------------------------------+
//| Alerts once when a candle closes with a signal.                  |
//+------------------------------------------------------------------+
void CheckNewSignal(const datetime formingBar, const int last, const double atr)
{
   if(lastAlertBar == 0)
   {
      lastAlertBar = formingBar; // first run: don't alert on an old signal
      return;
   }
   if(formingBar == lastAlertBar)
      return;
   lastAlertBar = formingBar;

   int dir = BuyBuf[last] != EMPTY_VALUE ? 1 : (SellBuf[last] != EMPTY_VALUE ? -1 : 0);
   if(dir == 0)
      return;
   double entry = CurrentEntry(dir);
   string message = StringFormat("AuPilot: %s signal on %s %s near %s. Stop %s, target %s",
                                 dir > 0 ? "BUY" : "SELL", _Symbol, TfName(), DoubleToString(entry, _Digits),
                                 DoubleToString(entry - dir * InpStopATR * atr, _Digits),
                                 DoubleToString(entry + dir * InpTargetATR * atr, _Digits));
   Print(message);
   if(MQLInfoInteger(MQL_TESTER))
      return;
   if(InpPopupAlerts)
      Alert(message);
   if(InpPushAlerts && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED))
      if(!SendNotification(StringSubstr(message, 0, 255)))
         Print("Phone alert not sent, error ", GetLastError());
}

//+------------------------------------------------------------------+
void UpdatePanel(const int last, const double &close[], const double rsi, const double atr)
{
   double gap = FastBuf[last] - SlowBuf[last];
   bool fastAbove = gap > 0;
   int held = 0; // candles the fast EMA has stayed on the same side of the slow EMA
   for(int i = last; i > 0 && held < 1000 && ((FastBuf[i] - SlowBuf[i]) > 0) == fastAbove; i--)
      held++;

   int lowest = last - 5000;
   if(lowest < InpTrendEMA)
      lowest = InpTrendEMA;
   int lastSignal = -1, lastDir = 0;
   for(int i = last; i >= lowest; i--)
   {
      if(BuyBuf[i] != EMPTY_VALUE)  { lastSignal = i; lastDir = 1;  break; }
      if(SellBuf[i] != EMPTY_VALUE) { lastSignal = i; lastDir = -1; break; }
   }

   bool trendUp = close[last] > TrendBuf[last];
   string fast = IntegerToString(InpFastEMA), slow = IntegerToString(InpSlowEMA);
   string trendText = trendUp
      ? "UP    price above " + IntegerToString(InpTrendEMA) + " EMA (buys only)"
      : "DOWN  price below " + IntegerToString(InpTrendEMA) + " EMA (sells only)";
   string rsiState = rsi >= InpRSIOverbought ? "overbought (no new buys)"
                   : (rsi <= InpRSIOversold ? "oversold (no new sells)" : "neutral");

   DrawBackground();
   SetRow(0, "AuPilot  " + _Symbol + " " + TfName(), clrWhite);
   SetRow(1, "Trend    " + trendText, trendUp ? clrLimeGreen : clrTomato);
   SetRow(2, StringFormat("%s/%s    %s %s %s for %s", fast, slow, fast,
                          fastAbove ? "above" : "below", slow, Candles(held)), fastAbove ? clrLimeGreen : clrTomato);
   SetRow(3, StringFormat("RSI      %.1f  %s", rsi, rsiState), clrGainsboro);
   SetRow(4, StringFormat("ATR      %s  (stop %.1fx, target %.1fx)", DoubleToString(atr, _Digits),
                          InpStopATR, InpTargetATR), clrGainsboro);

   if(lastSignal == last)
   {
      double entry  = CurrentEntry(lastDir);
      double stop   = entry - lastDir * InpStopATR * atr;
      double target = entry + lastDir * InpTargetATR * atr;
      double riskMoney = 0;
      double lots = LotsFor(entry, stop, riskMoney);
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      color signalColor = lastDir > 0 ? clrLimeGreen : clrTomato;
      SetRow(5, "Signal   " + (lastDir > 0 ? "BUY" : "SELL") + " on the last closed candle", signalColor);
      SetRow(6, StringFormat("Plan     entry ~%s  stop %s  target %s", DoubleToString(entry, _Digits),
                             DoubleToString(stop, _Digits), DoubleToString(target, _Digits)), signalColor);
      if(lots > 0)
         SetRow(7, StringFormat("Size     %s lots risks %.2f %s (%.1f%%)", DoubleToString(lots, VolumeDigits()),
                                riskMoney, AccountInfoString(ACCOUNT_CURRENCY),
                                equity > 0 ? riskMoney / equity * 100 : 0.0), clrGainsboro);
      else
         SetRow(7, "Size     below the broker's minimum lot at this risk", clrGainsboro);
   }
   else
   {
      SetRow(5, "Signal   none. Waiting for a " + fast + "/" + slow + " crossover", clrSilver);
      SetRow(6, "Plan     no trade now", clrSilver);
      SetRow(7, "Size     -", clrSilver);
   }

   if(lastSignal == last)
      SetRow(8, "Last     this one, closed at " + DoubleToString(close[lastSignal], _Digits), clrGainsboro);
   else if(lastSignal >= 0)
      SetRow(8, StringFormat("Last     %s %s ago at %s", lastDir > 0 ? "BUY" : "SELL",
                             Candles(last - lastSignal), DoubleToString(close[lastSignal], _Digits)), clrGainsboro);
   else
      SetRow(8, "Last     no signals on this chart yet", clrGainsboro);

   SetRow(9, _Period == PERIOD_H1 ? " " : "Note     built for H1 charts; this one is " + TfName(), clrGold);
   SetRow(10, "Rules only, not a prediction. Many signals lose.", clrGray);
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
void DrawBackground()
{
   string name = PREFIX + "bg";
   if(ObjectFind(0, name) >= 0)
      return;
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PANEL_X);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PANEL_Y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, PANEL_W);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, 16 + ROWS * ROW_H);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, C'24,26,32');
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_COLOR, C'70,74,84');
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

void SetRow(const int row, const string text, const color clr)
{
   string name = PREFIX + IntegerToString(row);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PANEL_X + 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PANEL_Y + 8 + row * ROW_H);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
double CurrentEntry(const int dir)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return iClose(_Symbol, _Period, 0);
   return dir > 0 ? tick.ask : tick.bid;
}

//+------------------------------------------------------------------+
//| Same sizing as AuBot: lots that lose InpRiskPct of equity at the |
//| stop, capped at InpMaxLeverage, rounded down to the lot step.    |
//+------------------------------------------------------------------+
double LotsFor(const double entry, const double stop, double &riskMoney)
{
   riskMoney = 0;
   double equity        = AccountInfoDouble(ACCOUNT_EQUITY);
   double tickSize      = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue     = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickValueLoss = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(equity <= 0 || tickSize <= 0 || tickValue <= 0 || tickValueLoss <= 0 || entry == stop)
      return 0;

   double lossPerLot  = MathAbs(entry - stop) / tickSize * tickValueLoss;
   double valuePerLot = entry / tickSize * tickValue;
   double lots = MathMin(equity * InpRiskPct / 100.0 / lossPerLot,
                         equity * InpMaxLeverage / valuePerLot);

   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0)
      lots = MathFloor(lots / step + 1e-9) * step;
   lots = MathMin(lots, maxLot);
   if(lots < minLot)
      return 0;
   riskMoney = lots * lossPerLot;
   return lots;
}

int VolumeDigits()
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   int digits = 0;
   while(digits < 8 && MathAbs(step * MathPow(10, digits) - MathRound(step * MathPow(10, digits))) > 1e-8)
      digits++;
   return digits;
}

string Candles(const int n)
{
   return n == 1 ? "1 candle" : IntegerToString(n) + " candles";
}

string TfName()
{
   return StringSubstr(EnumToString((ENUM_TIMEFRAMES)_Period), 7); // "PERIOD_H1" -> "H1"
}
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| AuBot.mq5                                                        |
//| Trend-following Expert Advisor for XAUUSD (gold) and AUDUSD.     |
//|                                                                  |
//| Same rules as the Python aubot, evaluated on each closed candle: |
//|  Long : fast EMA crosses above slow EMA, close above trend EMA,  |
//|         RSI below the overbought level.                          |
//|  Short: fast EMA crosses below slow EMA, close below trend EMA,  |
//|         RSI above the oversold level.                            |
//|  Exit : the opposite crossover, or the ATR-based stop-loss /     |
//|         take-profit attached to every order.                     |
//| Each trade risks a fixed % of equity. Works on the chart's       |
//| symbol, whatever the broker calls it (XAUUSD, GOLD, XAUUSD.m...).|
//+------------------------------------------------------------------+
#property copyright "aubot"
#property version   "1.00"
#property description "EMA crossover trend bot: 200 EMA filter, RSI guard, ATR stop/target, % risk sizing, daily loss limit and phone alerts."

#include <Trade\Trade.mqh>

input group "Strategy"
input ENUM_TIMEFRAMES InpTimeframe     = PERIOD_H1; // Candle timeframe
input int             InpFastEMA       = 20;        // Fast EMA period
input int             InpSlowEMA       = 50;        // Slow EMA period
input int             InpTrendEMA      = 200;       // Trend-filter EMA period
input int             InpRSIPeriod     = 14;        // RSI period
input double          InpRSIOverbought = 70;        // No new longs at or above this RSI
input double          InpRSIOversold   = 30;        // No new shorts at or below this RSI
input int             InpATRPeriod     = 14;        // ATR period
input double          InpStopATR       = 2.0;       // Stop-loss distance in ATRs
input double          InpTargetATR     = 3.0;       // Take-profit distance in ATRs

input group "Risk"
input double InpRiskPct         = 1.0;  // % of equity lost if the stop-loss is hit
input double InpMaxLeverage     = 10.0; // Max position value / equity
input double InpMaxDailyLossPct = 3.0;  // No new trades after this % daily loss

input group "Alerts"
input bool InpPushAlerts  = true;  // Push alerts to the MT5 phone app
input bool InpPopupAlerts = false; // Pop-up alerts on the computer

input group "Advanced"
input ulong InpMagic          = 20261015; // Magic number (marks this EA's trades)
input int   InpSlippagePoints = 30;       // Max slippage in points

struct Signals
{
   bool   crossUp;
   bool   crossDown;
   double close;
   double trend;
   double rsi;
   double atr;
};

CTrade   trade;
int      hFast  = INVALID_HANDLE;
int      hSlow  = INVALID_HANDLE;
int      hTrend = INVALID_HANDLE;
int      hRSI   = INVALID_HANDLE;
int      hATR   = INVALID_HANDLE;
datetime lastBar        = 0;
long     dayKey         = -1;
double   dayStartEquity = 0;
long     haltAlertDay   = -1;
bool     algoAlerted    = false;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpFastEMA <= 0 || InpFastEMA >= InpSlowEMA)
   {
      Print("Fast EMA must be positive and smaller than the slow EMA");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpStopATR <= 0 || InpTargetATR <= 0 || InpRiskPct <= 0 || InpRiskPct > 10 || InpMaxLeverage <= 0)
   {
      Print("Stop/target ATRs and leverage must be positive, and risk between 0 and 10%");
      return INIT_PARAMETERS_INCORRECT;
   }

   hFast  = iMA(_Symbol, Tf(), InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, Tf(), InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hTrend = iMA(_Symbol, Tf(), InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI   = iRSI(_Symbol, Tf(), InpRSIPeriod, PRICE_CLOSE);
   hATR   = iATR(_Symbol, Tf(), InpATRPeriod);
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hTrend == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE)
   {
      Print("Could not create indicators, error ", GetLastError());
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
   Notify(StringFormat("aubot started: %s %s, %s account, equity %.2f %s",
                       _Symbol, TfName(), demo ? "demo" : "REAL MONEY",
                       AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoString(ACCOUNT_CURRENCY)));
   if(InpPushAlerts && !InTester() && !TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED))
      Print("Phone alerts are off: in MT5 open Tools > Options > Notifications, ",
            "enable push notifications and enter the MetaQuotes ID from the MT5 phone app");
   CheckAlgoTrading();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   IndicatorRelease(hTrend);
   IndicatorRelease(hRSI);
   IndicatorRelease(hATR);
   // settings changes and recompiles restart the EA straight away; don't alert for those
   if(reason != REASON_CHARTCHANGE && reason != REASON_PARAMETERS && reason != REASON_RECOMPILE)
      Notify("aubot stopped on " + _Symbol + ". Open trades keep their stop-loss and take-profit.");
}

//+------------------------------------------------------------------+
void OnTick()
{
   bool halted = DailyLossHit();

   datetime bar = iTime(_Symbol, Tf(), 0);
   if(bar == 0 || bar == lastBar)
      return;

   Signals s;
   if(!ReadSignals(s))
      return; // indicator data not ready yet; try again on the next tick

   bool primed = lastBar != 0;
   lastBar = bar;
   if(!primed)
   {
      // don't act on a signal that may be up to a candle old when the EA is attached
      Print("Primed on ", _Symbol, " ", TfName(), " candle at ", TimeToString(bar), "; acting from the next close");
      return;
   }

   ulong ticket = 0;
   int position = OurPosition(ticket); // +1 long, -1 short, 0 flat
   if(!InTester())
      PrintFormat("Bar %s close=%s rsi=%.1f atr=%s equity=%.2f position=%d",
                  TimeToString(iTime(_Symbol, Tf(), 1)), DoubleToString(s.close, _Digits), s.rsi,
                  DoubleToString(s.atr, _Digits), AccountInfoDouble(ACCOUNT_EQUITY), position);

   if((position > 0 && s.crossDown) || (position < 0 && s.crossUp))
   {
      if(trade.PositionClose(ticket) && trade.ResultRetcode() == TRADE_RETCODE_DONE)
         position = 0; // the close alert is sent from OnTradeTransaction
      else
         Notify(StringFormat("Could not close %s %s: %s. Its stop-loss and take-profit are still active.",
                             _Symbol, position > 0 ? "long" : "short", trade.ResultRetcodeDescription()));
   }

   int entry = 0;
   if(s.crossUp && s.close > s.trend && s.rsi < InpRSIOverbought)
      entry = 1;
   else if(s.crossDown && s.close < s.trend && s.rsi > InpRSIOversold)
      entry = -1;

   if(position == 0 && entry != 0)
   {
      if(halted)
         AlertHalt();
      else
         Enter(entry, s.atr);
   }
}

//+------------------------------------------------------------------+
//| Reports every close of this EA's trades: stop-loss, take-profit, |
//| opposite crossover or manual, with the realized profit/loss.     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(trans.deal))
      return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol)
      return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
      return;

   double pnl = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) +
                HistoryDealGetDouble(trans.deal, DEAL_SWAP) +
                HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
   double price = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   bool wasLong = HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_SELL;
   ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal, DEAL_REASON);
   long positionId = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);

   if(!OpenedByThisEA(positionId))
      return;

   Notify(StringFormat("%s: %s %s %s at %s. P&L %+.2f %s",
                       pnl >= 0 ? "WIN" : "LOSS", _Symbol, wasLong ? "long" : "short",
                       ReasonText(reason), DoubleToString(price, _Digits), pnl,
                       AccountInfoString(ACCOUNT_CURRENCY)));
}

//+------------------------------------------------------------------+
bool ReadSignals(Signals &s)
{
   if(Bars(_Symbol, Tf()) < InpTrendEMA + 3)
      return false;

   double fast[], slow[], trendBuf[], rsiBuf[], atrBuf[];
   ArraySetAsSeries(fast, true); // index 0 = last closed candle, 1 = the one before
   ArraySetAsSeries(slow, true);
   if(CopyBuffer(hFast, 0, 1, 2, fast) != 2 || CopyBuffer(hSlow, 0, 1, 2, slow) != 2 ||
      CopyBuffer(hTrend, 0, 1, 1, trendBuf) != 1 || CopyBuffer(hRSI, 0, 1, 1, rsiBuf) != 1 ||
      CopyBuffer(hATR, 0, 1, 1, atrBuf) != 1)
      return false;

   double gapNow  = fast[0] - slow[0];
   double gapPrev = fast[1] - slow[1];
   s.crossUp   = gapNow > 0 && gapPrev <= 0;
   s.crossDown = gapNow < 0 && gapPrev >= 0;
   s.close     = iClose(_Symbol, Tf(), 1);
   s.trend     = trendBuf[0];
   s.rsi       = rsiBuf[0];
   s.atr       = atrBuf[0];
   return s.close > 0 && s.atr > 0;
}

//+------------------------------------------------------------------+
void Enter(const int dir, const double atr)
{
   if(!CheckAlgoTrading())
      return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   string side  = dir > 0 ? "long" : "short";
   double price = dir > 0 ? tick.ask : tick.bid;
   double sl    = RoundToTick(price - dir * InpStopATR * atr);
   double tp    = RoundToTick(price + dir * InpTargetATR * atr);

   double minDistance = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(MathAbs(price - sl) <= minDistance || MathAbs(tp - price) <= minDistance)
   {
      Notify(StringFormat("Skipped %s %s: stop-loss/take-profit closer than the broker allows", _Symbol, side));
      return;
   }

   double lots = LotSize(price, sl, side);
   if(lots <= 0)
      return;

   bool sent = dir > 0 ? trade.Buy(lots, _Symbol, price, sl, tp, "aubot")
                       : trade.Sell(lots, _Symbol, price, sl, tp, "aubot");
   uint code = trade.ResultRetcode();
   if(!sent || (code != TRADE_RETCODE_DONE && code != TRADE_RETCODE_DONE_PARTIAL && code != TRADE_RETCODE_PLACED))
   {
      Notify(StringFormat("%s %s order failed: %s", _Symbol, side, trade.ResultRetcodeDescription()));
      return;
   }

   double fill   = trade.ResultPrice() > 0 ? trade.ResultPrice() : price;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk   = MathAbs(fill - sl) / TickSize() * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS) * lots;
   Notify(StringFormat("%s %s %s lots at %s. Stop %s, target %s. Risk %.2f %s (%.1f%% of equity)",
                       dir > 0 ? "BUY" : "SELL", _Symbol, DoubleToString(lots, VolumeDigits()),
                       DoubleToString(fill, _Digits), DoubleToString(sl, _Digits), DoubleToString(tp, _Digits),
                       risk, AccountInfoString(ACCOUNT_CURRENCY), equity > 0 ? risk / equity * 100 : 0.0));
}

//+------------------------------------------------------------------+
//| Lots so that hitting the stop loses InpRiskPct of equity, capped |
//| at InpMaxLeverage. Uses the broker's tick values, so it works    |
//| for any contract size and account currency.                      |
//+------------------------------------------------------------------+
double LotSize(const double price, const double sl, const string side)
{
   double equity        = AccountInfoDouble(ACCOUNT_EQUITY);
   double tickSize      = TickSize();
   double tickValue     = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickValueLoss = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(equity <= 0 || tickSize <= 0 || tickValue <= 0 || tickValueLoss <= 0)
   {
      Notify("Skipped " + _Symbol + " " + side + ": symbol or account data unavailable");
      return 0;
   }

   double lossPerLot  = MathAbs(price - sl) / tickSize * tickValueLoss;
   double valuePerLot = price / tickSize * tickValue; // what 1 lot is worth in account currency
   double lots = MathMin(equity * InpRiskPct / 100.0 / lossPerLot,
                         equity * InpMaxLeverage / valuePerLot);

   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0)
      lots = MathFloor(lots / step + 1e-9) * step; // round down, never risk more than asked
   lots = MathMin(lots, maxLot);
   if(lots < minLot)
   {
      Notify(StringFormat("Skipped %s %s: %.1f%% risk allows only %.4f lots, below the broker minimum of %s",
                          _Symbol, side, InpRiskPct, lots, DoubleToString(minLot, VolumeDigits())));
      return 0;
   }
   return NormalizeDouble(lots, VolumeDigits());
}

//+------------------------------------------------------------------+
//| Tracks equity at the start of each broker-server day.            |
//+------------------------------------------------------------------+
bool DailyLossHit()
{
   long today = (long)(TimeCurrent() / 86400);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(today != dayKey)
   {
      dayKey = today;
      dayStartEquity = equity;
   }
   return dayStartEquity > 0 && equity <= dayStartEquity * (1 - InpMaxDailyLossPct / 100.0);
}

void AlertHalt()
{
   if(haltAlertDay == dayKey)
      return;
   haltAlertDay = dayKey;
   double change = (AccountInfoDouble(ACCOUNT_EQUITY) / dayStartEquity - 1) * 100;
   Notify(StringFormat("Daily loss limit hit on %s (%.1f%% today). No new trades until tomorrow.", _Symbol, change));
}

//+------------------------------------------------------------------+
int OurPosition(ulong &ticket)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i); // also selects the position
      if(t == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ticket = t;
      return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
   }
   ticket = 0;
   return 0;
}

bool OpenedByThisEA(const long positionId)
{
   if(!HistorySelectByPosition(positionId))
      return false;
   for(int i = 0; i < HistoryDealsTotal(); i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return (ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) == InpMagic;
   }
   return false;
}

string ReasonText(const ENUM_DEAL_REASON reason)
{
   switch(reason)
   {
      case DEAL_REASON_SL:     return "hit stop-loss";
      case DEAL_REASON_TP:     return "hit take-profit";
      case DEAL_REASON_EXPERT: return "closed on opposite crossover";
      case DEAL_REASON_SO:     return "closed by margin stop-out";
      case DEAL_REASON_CLIENT:
      case DEAL_REASON_MOBILE:
      case DEAL_REASON_WEB:    return "closed manually";
      default:                 return "closed";
   }
}

//+------------------------------------------------------------------+
bool CheckAlgoTrading()
{
   if(InTester())
      return true;
   bool allowed = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED);
   if(!allowed && !algoAlerted)
      Notify("aubot cannot trade: turn on the Algo Trading button in MT5, and tick " +
             "'Allow Algo Trading' in the EA's settings");
   algoAlerted = !allowed;
   return allowed;
}

void Notify(const string message)
{
   Print(message);
   if(InTester())
      return;
   if(InpPopupAlerts)
      Alert(message);
   if(InpPushAlerts && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED))
      if(!SendNotification(StringSubstr(message, 0, 255))) // push messages are limited to 255 chars
         Print("Phone alert not sent, error ", GetLastError());
}

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES Tf()
{
   return InpTimeframe == PERIOD_CURRENT ? (ENUM_TIMEFRAMES)_Period : InpTimeframe;
}

string TfName()
{
   return StringSubstr(EnumToString(Tf()), 7); // "PERIOD_H1" -> "H1"
}

bool InTester()
{
   return MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION);
}

double TickSize()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
}

double RoundToTick(const double price)
{
   double tick = TickSize();
   double rounded = tick > 0 ? MathRound(price / tick) * tick : price;
   return NormalizeDouble(rounded, _Digits);
}

int VolumeDigits()
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   int digits = 0;
   while(digits < 8 && MathAbs(step * MathPow(10, digits) - MathRound(step * MathPow(10, digits))) > 1e-8)
      digits++;
   return digits;
}
//+------------------------------------------------------------------+

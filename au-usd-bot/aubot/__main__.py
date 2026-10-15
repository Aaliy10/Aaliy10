"""Command line interface: `python -m aubot {backtest,fetch,run}`."""
from __future__ import annotations

import argparse
import logging
import sys

from .backtest import run_backtest
from .config import RiskParams, StrategyParams, get_instrument, load_dotenv
from .data import load_csv, save_csv, synthetic_candles

GRANULARITIES = ["M1", "M5", "M15", "M30", "H1", "H2", "H4", "H6", "H8", "H12", "D"]


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--instrument", default="AUD_USD",
                   help="AUD_USD (Australian dollar, default) or XAU_USD (gold)")
    g = p.add_argument_group("strategy")
    g.add_argument("--fast", type=int, default=StrategyParams.fast_ema, help="fast EMA period")
    g.add_argument("--slow", type=int, default=StrategyParams.slow_ema, help="slow EMA period")
    g.add_argument("--trend", type=int, default=StrategyParams.trend_ema, help="trend-filter EMA period")
    g.add_argument("--stop-atr", type=float, default=StrategyParams.stop_atr, help="stop-loss in ATRs")
    g.add_argument("--target-atr", type=float, default=StrategyParams.target_atr, help="take-profit in ATRs")
    r = p.add_argument_group("risk")
    r.add_argument("--risk-pct", type=float, default=RiskParams.risk_per_trade * 100,
                   help="percent of equity risked per trade (default 1)")
    r.add_argument("--max-leverage", type=float, default=RiskParams.max_leverage)
    r.add_argument("--max-daily-loss-pct", type=float, default=RiskParams.max_daily_loss * 100,
                   help="live bot stops opening trades after this daily loss (default 3)")


def _params(args: argparse.Namespace) -> tuple[StrategyParams, RiskParams]:
    strategy = StrategyParams(fast_ema=args.fast, slow_ema=args.slow, trend_ema=args.trend,
                              stop_atr=args.stop_atr, target_atr=args.target_atr)
    risk = RiskParams(risk_per_trade=args.risk_pct / 100, max_leverage=args.max_leverage,
                      max_daily_loss=args.max_daily_loss_pct / 100)
    return strategy, risk


def cmd_backtest(args: argparse.Namespace) -> int:
    instrument = get_instrument(args.instrument)
    strategy, risk = _params(args)
    if args.csv:
        candles = load_csv(args.csv)
    else:
        print("Using synthetic data (pass --csv for real prices). "
              "Synthetic results say nothing about real performance.\n")
        candles = synthetic_candles(instrument, bars=args.bars, seed=args.seed)

    result = run_backtest(candles, instrument, strategy, risk, args.equity, args.spread)
    print(result.summary())
    if args.trades_out:
        result.trades_frame().to_csv(args.trades_out, index=False)
        print(f"\nTrades written to {args.trades_out}")
    if args.equity_out:
        result.equity.to_csv(args.equity_out, index_label="time")
        print(f"Equity curve written to {args.equity_out}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from .oanda import OandaClient

    instrument = get_instrument(args.instrument)
    client = OandaClient.from_env(args.env)
    candles = client.history(instrument.symbol, args.granularity, args.count)
    save_csv(candles, args.out)
    if candles.empty:
        print("No candles returned")
    else:
        print(f"Saved {len(candles)} {instrument.label} {args.granularity} candles "
              f"({candles.index[0]} -> {candles.index[-1]}) to {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .live import TradingBot
    from .oanda import OandaClient

    if args.live and not args.i_understand_the_risks:
        print("Refusing to trade real money without --i-understand-the-risks", file=sys.stderr)
        return 2
    environment = "live" if args.live else "practice"
    instrument = get_instrument(args.instrument)
    strategy, risk = _params(args)
    client = OandaClient.from_env(environment)
    nav = client.nav()  # fail fast on bad credentials instead of retrying forever
    logging.getLogger("aubot").info("Connected to OANDA %s account %s (NAV %.2f)",
                                    environment, client.account_id, nav)

    bot = TradingBot(client, instrument, strategy, risk, args.granularity, dry_run=args.dry_run)
    try:
        bot.run(poll_seconds=args.poll)
    except KeyboardInterrupt:
        logging.getLogger("aubot").info("Stopped (open positions keep their stop-loss/take-profit)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aubot", description="Trend-following bot for AUD/USD and XAU/USD")
    parser.add_argument("--log-file", help="also write logs to this file")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="test the strategy on historical or synthetic candles")
    _add_common(bt)
    bt.add_argument("--csv", help="OHLC candle CSV (e.g. from `aubot fetch`); synthetic data if omitted")
    bt.add_argument("--bars", type=int, default=6000, help="synthetic bars to generate")
    bt.add_argument("--seed", type=int, default=7, help="synthetic data random seed")
    bt.add_argument("--equity", type=float, default=10_000.0, help="starting equity in USD")
    bt.add_argument("--spread", type=float, help="spread in price units (default: instrument preset)")
    bt.add_argument("--trades-out", help="write the trade list to this CSV")
    bt.add_argument("--equity-out", help="write the equity curve to this CSV")
    bt.set_defaults(func=cmd_backtest)

    fe = sub.add_parser("fetch", help="download historical candles from OANDA to CSV")
    fe.add_argument("--instrument", default="AUD_USD")
    fe.add_argument("--granularity", default="H1", choices=GRANULARITIES)
    fe.add_argument("--count", type=int, default=10_000, help="number of candles")
    fe.add_argument("--env", default="practice", choices=["practice", "live"])
    fe.add_argument("--out", required=True, help="output CSV path")
    fe.set_defaults(func=cmd_fetch)

    run = sub.add_parser("run", help="trade on OANDA (practice account unless --live)")
    _add_common(run)
    run.add_argument("--granularity", default="H1", choices=GRANULARITIES)
    run.add_argument("--poll", type=float, default=30.0, help="seconds between checks")
    run.add_argument("--dry-run", action="store_true", help="log signals and orders without placing them")
    run.add_argument("--live", action="store_true", help="use the real-money OANDA account")
    run.add_argument("--i-understand-the-risks", action="store_true", help="required together with --live")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as exc:  # OSError covers network errors
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

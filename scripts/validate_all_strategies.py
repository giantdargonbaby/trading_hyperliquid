from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Callable

from hyperliquid_trade_store.backtest import (
    BuyAndHoldStrategy,
    MomentumStrategy,
    SmaCrossStrategy,
    load_aligned_candles,
    run_backtest,
    write_backtest_outputs,
)
from strategies.example_strategy import ExampleStrategy
from strategies.myquant_classic_strategies import (
    MyquantBollTrendStrategy,
    MyquantMacdStrategy,
    MyquantMovingAverageStrategy,
    MyquantRsiStrategy,
    MyquantTurtleStrategy,
)
from strategies.whale_volume_strategy import WhaleMarketMakerStrategyV2, WhaleVolumeStrategy


StrategyFactory = Callable[[], object]


STRATEGY_SPECS: list[tuple[str, StrategyFactory, bool, dict]] = [
    ("buy-and-hold", lambda: BuyAndHoldStrategy(), False, {}),
    ("sma-cross-20-60", lambda: SmaCrossStrategy(fast=20, slow=60), False, {"fast": 20, "slow": 60}),
    ("momentum-120", lambda: MomentumStrategy(lookback=120, top_n=1), False, {"lookback": 120, "top_n": 1}),
    ("example-3-candle-momentum", lambda: ExampleStrategy(lookback=3), False, {"lookback": 3}),
    ("whale-volume-default", lambda: WhaleVolumeStrategy(), True, {}),
    (
        "whale-market-maker-v2",
        lambda: WhaleMarketMakerStrategyV2(),
        True,
        {
            "volume_lookback": 3,
            "spike_multiplier": 3,
            "exit_volume_ratio": 0.5,
            "exit_bars": 3,
            "step_weight": 0.05,
            "max_abs_weight": 0.3,
            "min_price_move_pct": 0.05,
            "cooldown_bars": 5,
            "max_hold_bars": 120,
            "stop_loss_pct": 0.4,
            "take_profit_pct": 0.6,
            "max_trades_per_day": 80,
        },
    ),
    (
        "whale-volume-tuned-24h",
        lambda: WhaleVolumeStrategy(
            volume_lookback=21,
            spike_multiplier=4.5,
            volume_drop_ratio=0.2,
            pullback_tolerance_pct=0.1,
            breakout_buffer_pct=0,
            step_weight=0.2,
            max_abs_weight=1,
        ),
        True,
        {
            "volume_lookback": 21,
            "spike_multiplier": 4.5,
            "volume_drop_ratio": 0.2,
            "pullback_tolerance_pct": 0.1,
            "breakout_buffer_pct": 0,
            "step_weight": 0.2,
            "max_abs_weight": 1,
        },
    ),
    (
        "myquant-ma-cross",
        lambda: MyquantMovingAverageStrategy(period=20, short_enabled=True),
        True,
        {"period": 20, "short_enabled": True},
    ),
    (
        "myquant-macd",
        lambda: MyquantMacdStrategy(fast_period=12, slow_period=26, signal_period=9, short_enabled=True),
        True,
        {"fast_period": 12, "slow_period": 26, "signal_period": 9, "short_enabled": True},
    ),
    (
        "myquant-rsi-long",
        lambda: MyquantRsiStrategy(period=14, oversold=30, overbought=70, short_enabled=False),
        False,
        {"period": 14, "oversold": 30, "overbought": 70, "short_enabled": False},
    ),
    (
        "myquant-boll-trend",
        lambda: MyquantBollTrendStrategy(period=20, deviation=2, short_enabled=True),
        True,
        {"period": 20, "deviation": 2, "short_enabled": True},
    ),
    (
        "myquant-turtle",
        lambda: MyquantTurtleStrategy(entry_period=20, exit_period=10, short_enabled=True),
        True,
        {"entry_period": 20, "exit_period": 10, "short_enabled": True},
    ),
]


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    rows = validate(args)
    write_comparison_csv(args.output_dir / "comparison.csv", rows)
    write_report(args.output_dir / "report.md", rows)
    print(args.output_dir / "report.md")
    for row in sorted(rows, key=lambda item: float(item["return_pct"]), reverse=True):
        print(
            row["strategy"],
            row["return_pct"],
            row["max_drawdown_pct"],
            row["trades"],
            row["total_fees"],
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate all local strategies on one recent candle window.")
    parser.add_argument("--db", type=Path, default=Path("data/hyperliquid.sqlite"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--coin", default="HYPE")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--lookback-hours", type=float, default=72.0)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/backtests/all_strategies_hype_3d"))
    return parser


def validate(args: argparse.Namespace) -> list[dict]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        end_time_ms = latest_candle_time_ms(conn, network=args.network, coin=args.coin, interval=args.interval)
        start_time_ms = end_time_ms - int(args.lookback_hours * 60 * 60 * 1000)
        aligned = load_aligned_candles(
            conn,
            network=args.network,
            coins=[args.coin],
            interval=args.interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_candles=20_000,
        )
        if len(aligned) < 2:
            raise RuntimeError("not enough candles for validation window")

        rows = []
        for strategy_label, factory, allow_short, params in STRATEGY_SPECS:
            strategy = factory()
            result = run_backtest(
                aligned,
                coins=[args.coin],
                strategy=strategy,
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                min_notional=1,
                allow_short=allow_short,
                max_gross_exposure=1,
                max_position_weight=1,
                strategy_name=strategy_label,
                network=args.network,
                interval=args.interval,
            )
            strategy_output = args.output_dir / strategy_label
            write_backtest_outputs(strategy_output, result)
            rows.append(
                {
                    "params": json.dumps(params, ensure_ascii=False),
                    "output_dir": str(strategy_output),
                    **result["summary"],
                }
            )
    finally:
        conn.close()
    return rows


def latest_candle_time_ms(conn: sqlite3.Connection, *, network: str, coin: str, interval: str) -> int:
    row = conn.execute(
        """
        SELECT max(open_time_ms) AS latest
        FROM market_candles
        WHERE network = ? AND coin = ? AND interval = ?
        """,
        (network, coin, interval),
    ).fetchone()
    if row is None or row["latest"] is None:
        raise RuntimeError(f"no candles found for {coin} {interval}")
    return int(row["latest"])


def write_comparison_csv(path: Path, rows: list[dict]) -> None:
    headers = [
        "strategy",
        "return_pct",
        "final_equity",
        "max_drawdown_pct",
        "trades",
        "total_fees",
        "sharpe_per_bar",
        "candles",
        "start_time_utc",
        "end_time_utc",
        "params",
        "output_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: float(item["return_pct"]), reverse=True):
            writer.writerow({header: row.get(header, "") for header in headers})


def write_report(path: Path, rows: list[dict]) -> None:
    first = rows[0]
    report = [
        "# All Strategies Validation",
        "",
        f"- Coin: {','.join(first['coins'])}",
        f"- Interval: {first['interval']}",
        f"- Range: {first['start_time_utc']} -> {first['end_time_utc']}",
        f"- Candles: {first['candles']}",
        "",
        "| Rank | Strategy | Return | Max DD | Trades | Fees | Sharpe/bar |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate(sorted(rows, key=lambda item: float(item["return_pct"]), reverse=True), start=1):
        report.append(
            f"| {rank} | {row['strategy']} | {row['return_pct']:.4f}% | "
            f"{row['max_drawdown_pct']:.4f}% | {row['trades']} | "
            f"{row['total_fees']:.4f} | {row['sharpe_per_bar']:.5f} |"
        )
    path.write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

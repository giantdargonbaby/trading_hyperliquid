from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from hyperliquid_trade_store.backtest import BuyAndHoldStrategy, load_aligned_candles, run_backtest, write_backtest_outputs
from hyperliquid_trade_store.time_utils import parse_time_ms
from strategies.myquant_classic_strategies import (
    MyquantBollTrendStrategy,
    MyquantMacdStrategy,
    MyquantMovingAverageStrategy,
    MyquantRsiStrategy,
    MyquantTurtleStrategy,
)


WINDOWS = [
    {
        "name": "hype_5m_7d",
        "interval": "5m",
        "start": "2026-06-29T06:25:00Z",
        "end": "2026-07-06T06:25:00Z",
    },
    {
        "name": "hype_1m_24h",
        "interval": "1m",
        "start": "2026-07-05T06:28:00Z",
        "end": "2026-07-06T06:28:00Z",
    },
]


STRATEGY_SPECS = [
    ("buy-and-hold", lambda: BuyAndHoldStrategy(), False, {}),
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
    output_dir = Path("reports/backtests/myquant_hype_validation")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = run_validation(output_dir)
    write_comparison_csv(output_dir / "comparison.csv", rows)
    write_report(output_dir / "report.md", rows)
    print(output_dir / "report.md")
    for row in sorted(rows, key=lambda item: (item["window"], -float(item["return_pct"]))):
        print(
            row["window"],
            row["strategy"],
            row["return_pct"],
            row["max_drawdown_pct"],
            row["trades"],
            row["total_fees"],
        )
    return 0


def run_validation(output_dir: Path) -> list[dict]:
    rows: list[dict] = []
    conn = sqlite3.connect("data/hyperliquid.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        for window in WINDOWS:
            aligned = load_aligned_candles(
                conn,
                network="mainnet",
                coins=["HYPE"],
                interval=window["interval"],
                start_time_ms=parse_time_ms(window["start"]),
                end_time_ms=parse_time_ms(window["end"]),
                max_candles=20_000,
            )
            if len(aligned) < 2:
                raise RuntimeError(f"not enough candles for {window['name']}")

            for strategy_label, factory, allow_short, params in STRATEGY_SPECS:
                strategy = factory()
                result = run_backtest(
                    aligned,
                    coins=["HYPE"],
                    strategy=strategy,
                    initial_cash=10_000,
                    fee_bps=4,
                    slippage_bps=0,
                    min_notional=1,
                    allow_short=allow_short,
                    max_gross_exposure=1,
                    max_position_weight=1,
                    strategy_name=strategy_label,
                    network="mainnet",
                    interval=window["interval"],
                )
                strategy_output = output_dir / window["name"] / strategy_label
                write_backtest_outputs(strategy_output, result)
                rows.append(
                    {
                        "window": window["name"],
                        "params": json.dumps(params, ensure_ascii=False),
                        "output_dir": str(strategy_output),
                        **result["summary"],
                    }
                )
    finally:
        conn.close()
    return rows


def write_comparison_csv(path: Path, rows: list[dict]) -> None:
    headers = [
        "window",
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
        for row in sorted(rows, key=lambda item: (item["window"], -float(item["return_pct"]))):
            writer.writerow({header: row.get(header, "") for header in headers})


def write_report(path: Path, rows: list[dict]) -> None:
    report = [
        "# MyQuant Strategy Validation",
        "",
        "Source: myquant/strategy Apache-2.0; local adapters run against Hyperliquid candle data.",
        "",
    ]
    for window in WINDOWS:
        window_rows = [row for row in rows if row["window"] == window["name"]]
        first = window_rows[0]
        report.append(f"## {window['name']}")
        report.append(f"Range: {first['start_time_utc']} -> {first['end_time_utc']}; candles={first['candles']}")
        report.append("")
        report.extend(markdown_table(window_rows))
        report.append("")
    path.write_text("\n".join(report), encoding="utf-8")


def markdown_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 策略 | 收益率 | 最大回撤 | 交易数 | 手续费 | Sharpe/bar |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(rows, key=lambda item: float(item["return_pct"]), reverse=True):
        lines.append(
            f"| {row['strategy']} | {row['return_pct']:.4f}% | {row['max_drawdown_pct']:.4f}% | "
            f"{row['trades']} | {row['total_fees']:.4f} | {row['sharpe_per_bar']:.5f} |"
        )
    return lines


if __name__ == "__main__":
    raise SystemExit(main())

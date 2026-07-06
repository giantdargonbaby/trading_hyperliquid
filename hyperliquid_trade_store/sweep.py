from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from hyperliquid_trade_store.backtest import (
    auto_fetch_market_candles,
    load_aligned_candles,
    load_strategy,
    parse_coins,
    parse_scalar,
    parse_strategy_params,
    resolve_fetch_window,
    run_backtest,
    strategy_name,
    write_backtest_outputs,
)
from hyperliquid_trade_store.storage import connect, init_db
from hyperliquid_trade_store.time_utils import parse_time_ms


SUMMARY_COLUMNS = [
    "return_pct",
    "final_equity",
    "max_drawdown_pct",
    "trades",
    "total_fees",
    "sharpe_per_bar",
    "candles",
    "start_time_utc",
    "end_time_utc",
    "strategy",
    "network",
    "coins",
    "interval",
    "initial_cash",
]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    coins = parse_coins(args)
    if not coins:
        parser.error("provide at least one --coin or --coins value")

    fixed_params = parse_strategy_params(args.param)
    grid, grid_keys = parse_sweep_params(args.sweep_param)
    param_keys = ordered_unique([*fixed_params.keys(), *grid_keys])
    start_time_ms = parse_time_ms(args.start)
    end_time_ms = parse_time_ms(args.end)

    conn = connect(args.db)
    try:
        init_db(conn)
        aligned = load_aligned_candles(
            conn,
            network=args.network,
            coins=coins,
            interval=args.interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_candles=args.max_candles,
        )
        if len(aligned) < 2 and args.auto_fetch:
            fetch_start_time_ms, fetch_end_time_ms = resolve_fetch_window(
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                lookback_hours=args.lookback_hours,
            )
            fetched = auto_fetch_market_candles(
                conn,
                network=args.network,
                coins=coins,
                interval=args.interval,
                start_time_ms=fetch_start_time_ms,
                end_time_ms=fetch_end_time_ms,
                timeout=args.fetch_timeout,
            )
            print("fetched candles: " + ", ".join(f"{coin}={count}" for coin, count in fetched.items()))
            aligned = load_aligned_candles(
                conn,
                network=args.network,
                coins=coins,
                interval=args.interval,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_candles=args.max_candles,
            )
    finally:
        conn.close()

    if len(aligned) < 2:
        print(
            "not enough aligned candles. Collect more market_candles for the requested coins, interval, and time range.",
            file=sys.stderr,
        )
        return 1

    rows, best_result = run_parameter_sweep(
        aligned,
        coins=coins,
        strategy_spec=args.strategy,
        fixed_params=fixed_params,
        grid=grid,
        param_keys=param_keys,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        min_notional=args.min_notional,
        allow_short=args.allow_short,
        max_gross_exposure=args.max_gross_exposure,
        max_position_weight=args.max_position_weight,
        network=args.network,
        interval=args.interval,
    )

    write_sweep_outputs(args.output_dir, rows, param_keys, best_result=best_result, top=args.top)
    print_summary(rows, args.output_dir, args.top)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperliquid-sweep",
        description="Run a parameter grid search for a backtest strategy.",
    )
    parser.add_argument("--db", default="data/hyperliquid.sqlite", type=Path, help="SQLite database path.")
    parser.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    parser.add_argument("--coin", action="append", default=[], help="Coin to include. Repeat for multiple coins.")
    parser.add_argument("--coins", help="Comma-separated coins, for example BTC,ETH,SOL.")
    parser.add_argument("--interval", default="1m", help='Candle interval, for example "1m", "5m", or "1h".')
    parser.add_argument("--start", help="Start time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--end", help="End time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--lookback-hours", type=float, default=168.0, help="Auto-fetch lookback when --start is omitted.")
    parser.add_argument("--no-auto-fetch", dest="auto_fetch", action="store_false", help="Do not fetch missing candles before sweep.")
    parser.add_argument("--fetch-timeout", type=float, default=20.0, help="HTTP timeout for auto-fetch requests.")
    parser.add_argument("--max-candles", type=int, default=20_000, help="Maximum candles loaded per coin.")
    parser.add_argument("--strategy", default="strategies/whale_volume_strategy.py:WhaleVolumeStrategy")
    parser.add_argument("--param", action="append", default=[], help="Fixed strategy parameter as key=value. Repeatable.")
    parser.add_argument(
        "--sweep-param",
        action="append",
        default=[],
        help="Parameter grid as key=value1,value2. Repeatable, for example --sweep-param step_weight=0.1,0.2.",
    )
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=4.0, help="Fee in basis points per trade.")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Slippage in basis points per trade.")
    parser.add_argument("--min-notional", type=float, default=1.0, help="Ignore trades below this notional.")
    parser.add_argument("--allow-short", action="store_true", help="Allow negative target weights.")
    parser.add_argument("--max-gross-exposure", type=float, default=1.0, help="Maximum sum(abs(weights)).")
    parser.add_argument("--max-position-weight", type=float, default=1.0, help="Maximum absolute weight per coin.")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/backtests/parameter_sweep"))
    parser.add_argument("--top", type=int, default=20, help="Rows to show in the markdown report and stdout.")
    parser.set_defaults(auto_fetch=True)
    return parser


def parse_sweep_params(values: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    values_by_key: dict[str, list[Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --sweep-param {value!r}; expected key=value1,value2")
        key, raw_values = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --sweep-param {value!r}; key cannot be empty")
        if key in values_by_key:
            raise ValueError(f"duplicate --sweep-param key {key!r}")
        parsed_values = parse_value_list(raw_values)
        if not parsed_values:
            raise ValueError(f"invalid --sweep-param {value!r}; provide at least one value")
        values_by_key[key] = parsed_values

    keys = list(values_by_key)
    if not keys:
        return [{}], []

    grid = [dict(zip(keys, combo)) for combo in itertools.product(*(values_by_key[key] for key in keys))]
    return grid, keys


def parse_value_list(value: str) -> list[Any]:
    return [parse_scalar(part.strip()) for part in value.split(",") if part.strip()]


def run_parameter_sweep(
    aligned,
    *,
    coins: list[str],
    strategy_spec: str,
    fixed_params: dict[str, Any],
    grid: list[dict[str, Any]],
    param_keys: list[str],
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
    min_notional: float,
    allow_short: bool,
    max_gross_exposure: float,
    max_position_weight: float,
    network: str,
    interval: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    best_result: dict[str, Any] | None = None
    best_score: tuple[float, float, float, float] | None = None

    for run_id, variable_params in enumerate(grid, start=1):
        params = dict(fixed_params)
        params.update(variable_params)
        strategy = load_strategy(strategy_spec, params)
        result = run_backtest(
            aligned,
            coins=coins,
            strategy=strategy,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            min_notional=min_notional,
            allow_short=allow_short,
            max_gross_exposure=max_gross_exposure,
            max_position_weight=max_position_weight,
            strategy_name=strategy_name(strategy),
            network=network,
            interval=interval,
        )
        summary = result["summary"]
        row = {"run_id": run_id, **{key: params.get(key) for key in param_keys}, **summary}
        rows.append(row)

        score = score_row(row)
        if best_score is None or score > best_score:
            best_score = score
            best_result = {"params": params, "row": row, "result": result}

    sorted_rows = sorted(rows, key=score_row, reverse=True)
    for rank, row in enumerate(sorted_rows, start=1):
        row["rank"] = rank

    if best_result is None:
        raise ValueError("sweep grid is empty")
    best_result["row"] = next(row for row in sorted_rows if row["run_id"] == best_result["row"]["run_id"])
    return sorted_rows, best_result


def score_row(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row["return_pct"]),
        float(row["max_drawdown_pct"]),
        float(row["sharpe_per_bar"]),
        -float(row["trades"]),
    )


def write_sweep_outputs(
    output_dir: Path,
    rows: list[dict[str, Any]],
    param_keys: list[str],
    *,
    best_result: dict[str, Any],
    top: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "sweep_results.csv", rows, param_keys)
    (output_dir / "sweep_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(output_dir / "sweep_report.md", rows, param_keys, best_result=best_result, top=top)
    (output_dir / "best_params.json").write_text(
        json.dumps(best_result["params"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_backtest_outputs(output_dir / "best_run", best_result["result"])


def write_csv(path: Path, rows: list[dict[str, Any]], param_keys: list[str]) -> None:
    headers = ["rank", "run_id", *param_keys, *SUMMARY_COLUMNS]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: format_cell(row.get(key)) for key in headers})


def write_markdown(
    path: Path,
    rows: list[dict[str, Any]],
    param_keys: list[str],
    *,
    best_result: dict[str, Any],
    top: int,
) -> None:
    positive_count = sum(1 for row in rows if float(row["return_pct"]) > 0)
    best_row = best_result["row"]
    lines = [
        "# Parameter Sweep Report",
        "",
        f"- Runs: {len(rows)}",
        f"- Positive return runs: {positive_count}",
        f"- Best return: {best_row['return_pct']}%",
        f"- Best params: {format_params(best_result['params'])}",
        f"- Data range: {best_row['start_time_utc']} -> {best_row['end_time_utc']}",
        "",
        f"## Top {min(top, len(rows))}",
        "",
    ]
    lines.extend(markdown_table(rows[:top], param_keys))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def markdown_table(rows: list[dict[str, Any]], param_keys: list[str]) -> list[str]:
    headers = ["rank", "return_pct", "max_drawdown_pct", "trades", "total_fees", "sharpe_per_bar", *param_keys]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(format_cell(row.get(key))) for key in headers) + " |")
    return lines


def print_summary(rows: list[dict[str, Any]], output_dir: Path, top: int) -> None:
    positive_count = sum(1 for row in rows if float(row["return_pct"]) > 0)
    print(f"output: {output_dir}")
    print(f"runs: {len(rows)}")
    print(f"positive_return_runs: {positive_count}")
    for row in rows[:top]:
        print(
            f"#{row['rank']} return={row['return_pct']}% drawdown={row['max_drawdown_pct']}% "
            f"trades={row['trades']} params={format_params(extract_params(row))}"
        )


def extract_params(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"rank", "run_id", *SUMMARY_COLUMNS}}


def format_params(params: dict[str, Any]) -> str:
    if not params:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in params.items())


def format_cell(value: Any) -> Any:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return value


def ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

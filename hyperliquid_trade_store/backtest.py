from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from hyperliquid_trade_store.collector import HyperliquidCollector
from hyperliquid_trade_store.logging_utils import (
    format_list,
    format_params as format_log_params,
    format_path,
    log_error,
    log_info,
)
from hyperliquid_trade_store.storage import begin_run, connect, finish_run, init_db, upsert_market_candles
from hyperliquid_trade_store.time_utils import ms_to_utc_iso, parse_time_ms, utc_now_ms


@dataclass(frozen=True)
class Candle:
    coin: str
    time_ms: int
    time_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades_count: int


@dataclass
class Trade:
    time_ms: int
    time_utc: str
    coin: str
    side: str
    quantity: float
    price: float
    notional: float
    fee: float
    cash_after: float


@dataclass
class EquityPoint:
    time_ms: int
    time_utc: str
    equity: float
    cash: float
    drawdown_pct: float
    close_prices: dict[str, float]
    positions: dict[str, float]
    notionals: dict[str, float]


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, float]
    total_fees: float = 0.0

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.positions.get(coin, 0.0) * prices[coin] for coin in prices)

    def rebalance(
        self,
        *,
        target_weights: dict[str, float],
        opens: dict[str, float],
        time_ms: int,
        time_utc: str,
        fee_bps: float,
        slippage_bps: float,
        min_notional: float,
    ) -> list[Trade]:
        equity_at_open = self.equity(opens)
        trades: list[Trade] = []

        for coin, open_price in opens.items():
            if open_price <= 0:
                continue
            weight = target_weights.get(coin, 0.0)
            current_units = self.positions.get(coin, 0.0)
            target_units = equity_at_open * weight / open_price
            delta_units = target_units - current_units
            raw_notional = abs(delta_units * open_price)
            if raw_notional < min_notional:
                continue

            side = "buy" if delta_units > 0 else "sell"
            slippage = slippage_bps / 10_000
            execution_price = open_price * (1 + slippage if side == "buy" else 1 - slippage)
            signed_notional = delta_units * execution_price
            fee = abs(signed_notional) * fee_bps / 10_000

            self.cash -= signed_notional
            self.cash -= fee
            self.positions[coin] = current_units + delta_units
            self.total_fees += fee
            trades.append(
                Trade(
                    time_ms=time_ms,
                    time_utc=time_utc,
                    coin=coin,
                    side=side,
                    quantity=abs(delta_units),
                    price=execution_price,
                    notional=abs(signed_notional),
                    fee=fee,
                    cash_after=self.cash,
                )
            )

        return trades


class Strategy(Protocol):
    name: str

    def generate_targets(
        self,
        *,
        histories: dict[str, list[Candle]],
        portfolio: Portfolio,
        timestamp_ms: int,
    ) -> dict[str, float]:
        """Return target portfolio weights by coin for the next execution."""


class BuyAndHoldStrategy:
    name = "buy-and-hold"

    def __init__(self) -> None:
        self.entered = False

    def generate_targets(
        self,
        *,
        histories: dict[str, list[Candle]],
        portfolio: Portfolio,
        timestamp_ms: int,
    ) -> dict[str, float]:
        if self.entered:
            return {}
        if not histories:
            return {}
        self.entered = True
        weight = 1.0 / len(histories)
        return {coin: weight for coin in histories}


class SmaCrossStrategy:
    name = "sma-cross"

    def __init__(self, fast: int = 20, slow: int = 50) -> None:
        if fast <= 0 or slow <= 0 or fast >= slow:
            raise ValueError("sma-cross requires 0 < fast < slow")
        self.fast = fast
        self.slow = slow

    def generate_targets(
        self,
        *,
        histories: dict[str, list[Candle]],
        portfolio: Portfolio,
        timestamp_ms: int,
    ) -> dict[str, float]:
        active: list[str] = []
        for coin, rows in histories.items():
            if len(rows) < self.slow:
                continue
            closes = [row.close for row in rows]
            fast_sma = sum(closes[-self.fast :]) / self.fast
            slow_sma = sum(closes[-self.slow :]) / self.slow
            if fast_sma > slow_sma:
                active.append(coin)

        if not active:
            return {coin: 0.0 for coin in histories}

        weight = 1.0 / len(active)
        return {coin: (weight if coin in active else 0.0) for coin in histories}


class MomentumStrategy:
    name = "momentum"

    def __init__(self, lookback: int = 60, top_n: int = 1) -> None:
        if lookback <= 0 or top_n <= 0:
            raise ValueError("momentum requires positive lookback and top_n")
        self.lookback = lookback
        self.top_n = top_n

    def generate_targets(
        self,
        *,
        histories: dict[str, list[Candle]],
        portfolio: Portfolio,
        timestamp_ms: int,
    ) -> dict[str, float]:
        scores: list[tuple[str, float]] = []
        for coin, rows in histories.items():
            if len(rows) < self.lookback + 1:
                continue
            start = rows[-self.lookback - 1].close
            end = rows[-1].close
            if start > 0:
                scores.append((coin, end / start - 1))

        selected = [coin for coin, score in sorted(scores, key=lambda item: item[1], reverse=True) if score > 0]
        selected = selected[: self.top_n]
        if not selected:
            return {coin: 0.0 for coin in histories}

        weight = 1.0 / len(selected)
        return {coin: (weight if coin in selected else 0.0) for coin in histories}


BUILTIN_STRATEGIES = {
    BuyAndHoldStrategy.name: BuyAndHoldStrategy,
    SmaCrossStrategy.name: SmaCrossStrategy,
    MomentumStrategy.name: MomentumStrategy,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    coins = parse_coins(args)
    if not coins:
        parser.error("provide at least one --coin or --coins value")

    params = parse_strategy_params(args.param)
    strategy = load_strategy(args.strategy, params)
    start_time_ms = parse_time_ms(args.start)
    end_time_ms = parse_time_ms(args.end)
    log_info(
        "input backtest "
        f"db={args.db} network={args.network} coins={format_list(coins)} interval={args.interval} "
        f"start={ms_to_utc_iso(start_time_ms)} end={ms_to_utc_iso(end_time_ms)} "
        f"strategy={args.strategy} params={format_log_params(params)}"
    )
    log_info(
        "options backtest "
        f"auto_fetch={args.auto_fetch} lookback_hours={args.lookback_hours} max_candles={args.max_candles} "
        f"initial_cash={args.initial_cash} fee_bps={args.fee_bps} slippage_bps={args.slippage_bps} "
        f"min_notional={args.min_notional} allow_short={args.allow_short} "
        f"max_gross_exposure={args.max_gross_exposure} max_position_weight={args.max_position_weight}"
    )

    log_info(f"load local candles db={args.db}")
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
        log_info(f"loaded aligned candles rows={len(aligned)}")

        if len(aligned) < 2 and args.auto_fetch:
            fetch_start_time_ms, fetch_end_time_ms = resolve_fetch_window(
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                lookback_hours=args.lookback_hours,
            )
            log_info(
                "local candles insufficient; auto fetch "
                f"coins={format_list(coins)} interval={args.interval} "
                f"start={ms_to_utc_iso(fetch_start_time_ms)} end={ms_to_utc_iso(fetch_end_time_ms)}"
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
            log_info("auto fetch output candles=" + ",".join(f"{coin}={count}" for coin, count in fetched.items()))
            log_info("reload local candles after auto fetch")
            aligned = load_aligned_candles(
                conn,
                network=args.network,
                coins=coins,
                interval=args.interval,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_candles=args.max_candles,
            )
            log_info(f"reloaded aligned candles rows={len(aligned)}")
    finally:
        conn.close()

    if len(aligned) < 2:
        log_error(
            "not enough aligned candles; collect more market_candles for the requested coins, interval, and time range"
        )
        print(
            "not enough aligned candles. Collect more market_candles for the requested coins, interval, and time range.",
            file=sys.stderr,
        )
        return 1

    resolved_strategy_name = strategy_name(strategy)
    log_info(f"run backtest strategy={resolved_strategy_name} candles={len(aligned)}")
    result = run_backtest(
        aligned,
        coins=coins,
        strategy=strategy,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        min_notional=args.min_notional,
        allow_short=args.allow_short,
        max_gross_exposure=args.max_gross_exposure,
        max_position_weight=args.max_position_weight,
        strategy_name=resolved_strategy_name,
        network=args.network,
        interval=args.interval,
    )

    output_dir = args.output_dir or default_output_dir(args.network, coins, args.interval, resolved_strategy_name)
    log_info(f"write backtest outputs dir={output_dir} files=summary.json,equity_curve.csv,trades.csv")
    write_backtest_outputs(output_dir, result)
    log_info(
        "output backtest "
        f"dir={output_dir} final_equity={result['summary']['final_equity']} "
        f"return_pct={result['summary']['return_pct']} trades={result['summary']['trades']}"
    )
    print_summary(result["summary"], output_dir)

    if args.write_baseline:
        log_info(f"write baseline output={args.write_baseline}")
        write_baseline(args.write_baseline, result["summary"])
        print(f"wrote baseline {args.write_baseline}")

    if args.baseline:
        log_info(f"compare baseline input={args.baseline} tolerance_pct={args.tolerance_pct}")
        ok, messages = compare_baseline(args.baseline, result["summary"], tolerance_pct=args.tolerance_pct)
        for message in messages:
            print(message)
        if not ok:
            log_error("baseline check failed")
            return 2
        log_info("baseline check passed")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperliquid-backtest",
        description="Run strategy backtests against stored Hyperliquid market_candles data.",
    )
    parser.add_argument("--db", default="data/hyperliquid.sqlite", type=Path, help="SQLite database path.")
    parser.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    parser.add_argument("--coin", action="append", default=[], help="Coin to include. Repeat for multiple coins.")
    parser.add_argument("--coins", help="Comma-separated coins, for example BTC,ETH,SOL.")
    parser.add_argument("--interval", default="1m", help='Candle interval, for example "1m", "5m", or "1h".')
    parser.add_argument("--start", help="Start time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--end", help="End time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--lookback-hours", type=float, default=168.0, help="Auto-fetch lookback when --start is omitted.")
    parser.add_argument("--no-auto-fetch", dest="auto_fetch", action="store_false", help="Do not fetch missing candles before backtest.")
    parser.add_argument("--fetch-timeout", type=float, default=20.0, help="HTTP timeout for auto-fetch requests.")
    parser.add_argument("--max-candles", type=int, default=20_000, help="Maximum candles loaded per coin.")
    parser.add_argument("--strategy", default="sma-cross", help="Built-in strategy name or path.py:ClassName.")
    parser.add_argument("--param", action="append", default=[], help="Strategy parameter as key=value. Repeatable.")
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=4.0, help="Fee in basis points per trade.")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Slippage in basis points per trade.")
    parser.add_argument("--min-notional", type=float, default=1.0, help="Ignore trades below this notional.")
    parser.add_argument("--allow-short", action="store_true", help="Allow negative target weights.")
    parser.add_argument("--max-gross-exposure", type=float, default=1.0, help="Maximum sum(abs(weights)).")
    parser.add_argument("--max-position-weight", type=float, default=1.0, help="Maximum absolute weight per coin.")
    parser.add_argument("--output-dir", type=Path, help="Directory for summary.json, equity_curve.csv, and trades.csv.")
    parser.add_argument("--write-baseline", type=Path, help="Write stable metrics to a baseline JSON file.")
    parser.add_argument("--baseline", type=Path, help="Compare this run to a baseline JSON file.")
    parser.add_argument("--tolerance-pct", type=float, default=0.01, help="Allowed metric drift in percent points.")
    parser.set_defaults(auto_fetch=True)
    return parser


def parse_coins(args: argparse.Namespace) -> list[str]:
    coins: list[str] = []
    for coin in args.coin:
        coins.extend(part.strip() for part in coin.split(","))
    if args.coins:
        coins.extend(part.strip() for part in args.coins.split(","))

    unique: list[str] = []
    seen: set[str] = set()
    for coin in coins:
        if coin and coin not in seen:
            unique.append(coin)
            seen.add(coin)
    return unique


def parse_strategy_params(values: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --param {value!r}; expected key=value")
        key, raw = value.split("=", 1)
        params[key.strip()] = parse_scalar(raw.strip())
    return params


def parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." not in value:
            return int(value)
        return float(value)
    except ValueError:
        return value


def load_strategy(spec: str, params: dict[str, Any]) -> Strategy:
    if spec in BUILTIN_STRATEGIES:
        return BUILTIN_STRATEGIES[spec](**params)

    if ":" not in spec:
        raise ValueError("custom strategy must be specified as path.py:ClassName")

    path_text, class_name = spec.split(":", 1)
    path = Path(path_text).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    module_name = f"hyperliquid_custom_strategy_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load strategy module {path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    strategy_class = getattr(module, class_name)
    strategy = strategy_class(**params)
    if not hasattr(strategy, "generate_targets"):
        raise TypeError("strategy must define generate_targets(...)")
    return strategy


def resolve_fetch_window(
    *,
    start_time_ms: int | None,
    end_time_ms: int | None,
    lookback_hours: float,
) -> tuple[int, int]:
    resolved_end_time_ms = end_time_ms or utc_now_ms()
    if start_time_ms is not None:
        return start_time_ms, resolved_end_time_ms
    lookback_ms = int(max(0.0, lookback_hours) * 60 * 60 * 1000)
    return max(0, resolved_end_time_ms - lookback_ms), resolved_end_time_ms


def auto_fetch_market_candles(
    conn: sqlite3.Connection,
    *,
    network: str,
    coins: list[str],
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    timeout: float,
    collector: HyperliquidCollector | None = None,
) -> dict[str, int]:
    collector = collector or HyperliquidCollector(network=network, timeout=timeout)
    fetched: dict[str, int] = {}
    for coin in coins:
        log_info(
            "auto fetch candles start "
            f"coin={coin} network={network} interval={interval} "
            f"start={ms_to_utc_iso(start_time_ms)} end={ms_to_utc_iso(end_time_ms)}"
        )
        run_id = begin_run(
            conn,
            network=network,
            target=f"public:{coin}:backtest-auto-fetch",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        try:
            total = 0
            for batch in collector.candle_batches(
                coin=coin,
                interval=interval,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            ):
                upserted = upsert_market_candles(conn, network=network, candles=batch)
                total += upserted
                log_info(f"auto fetch candle batch coin={coin} rows={len(batch)} upserted={upserted} total={total}")
            fetched[coin] = total
            finish_run(conn, run_id, status="success", message=f"candles={total}")
            log_info(f"auto fetch candles finished coin={coin} rows={total}")
        except Exception as error:
            finish_run(conn, run_id, status="failed", message=str(error))
            log_error(f"auto fetch candles failed coin={coin} error={error}")
            raise
    return fetched


def load_aligned_candles(
    conn: sqlite3.Connection,
    *,
    network: str,
    coins: list[str],
    interval: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    max_candles: int,
) -> list[tuple[int, dict[str, Candle]]]:
    by_coin = {
        coin: load_coin_candles(
            conn,
            network=network,
            coin=coin,
            interval=interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_candles=max_candles,
        )
        for coin in coins
    }
    if any(not rows for rows in by_coin.values()):
        return []

    common_times = set(by_coin[coins[0]])
    for coin in coins[1:]:
        common_times &= set(by_coin[coin])

    aligned: list[tuple[int, dict[str, Candle]]] = []
    for timestamp in sorted(common_times):
        aligned.append((timestamp, {coin: by_coin[coin][timestamp] for coin in coins}))
    return aligned


def load_coin_candles(
    conn: sqlite3.Connection,
    *,
    network: str,
    coin: str,
    interval: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    max_candles: int,
) -> dict[int, Candle]:
    filters = [
        "network = ?",
        "coin = ?",
        "interval = ?",
        "open IS NOT NULL",
        "high IS NOT NULL",
        "low IS NOT NULL",
        "close IS NOT NULL",
    ]
    params: list[Any] = [network, coin, interval]
    if start_time_ms is not None:
        filters.append("open_time_ms >= ?")
        params.append(start_time_ms)
    if end_time_ms is not None:
        filters.append("open_time_ms <= ?")
        params.append(end_time_ms)
    params.append(max(1, max_candles))

    rows = conn.execute(
        f"""
        SELECT open_time_ms, open_time_utc, open, high, low, close, volume, trades_count
        FROM market_candles
        WHERE {" AND ".join(filters)}
        ORDER BY open_time_ms ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    candles: dict[int, Candle] = {}
    for row in rows:
        candle = Candle(
            coin=coin,
            time_ms=int(row["open_time_ms"]),
            time_utc=str(row["open_time_utc"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if row["volume"] is not None else 0.0,
            trades_count=int(row["trades_count"]) if row["trades_count"] is not None else 0,
        )
        candles[candle.time_ms] = candle
    return candles


def run_backtest(
    aligned: list[tuple[int, dict[str, Candle]]],
    *,
    coins: list[str],
    strategy: Strategy,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
    min_notional: float,
    allow_short: bool,
    max_gross_exposure: float,
    max_position_weight: float,
    strategy_name: str,
    network: str,
    interval: str,
) -> dict[str, Any]:
    portfolio = Portfolio(cash=initial_cash, positions={coin: 0.0 for coin in coins})
    histories: dict[str, list[Candle]] = {coin: [] for coin in coins}
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []
    peak_equity = initial_cash
    returns: list[float] = []
    previous_equity = initial_cash

    for timestamp, bars in aligned:
        first_bar = next(iter(bars.values()))
        raw_targets = strategy.generate_targets(histories=histories, portfolio=portfolio, timestamp_ms=timestamp)
        opens = {coin: bars[coin].open for coin in coins}
        if raw_targets:
            target_weights = normalize_targets(
                raw_targets,
                coins=coins,
                allow_short=allow_short,
                max_gross_exposure=max_gross_exposure,
                max_position_weight=max_position_weight,
            )
            trades.extend(
                portfolio.rebalance(
                    target_weights=target_weights,
                    opens=opens,
                    time_ms=timestamp,
                    time_utc=first_bar.time_utc,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    min_notional=min_notional,
                )
            )

        closes = {coin: bars[coin].close for coin in coins}
        equity = portfolio.equity(closes)
        peak_equity = max(peak_equity, equity)
        drawdown_pct = (equity / peak_equity - 1) * 100 if peak_equity else 0.0
        returns.append(equity / previous_equity - 1 if previous_equity else 0.0)
        previous_equity = equity
        equity_curve.append(
            EquityPoint(
                time_ms=timestamp,
                time_utc=first_bar.time_utc,
                equity=equity,
                cash=portfolio.cash,
                drawdown_pct=drawdown_pct,
                close_prices=closes,
                positions=dict(portfolio.positions),
                notionals={coin: portfolio.positions.get(coin, 0.0) * closes[coin] for coin in coins},
            )
        )

        for coin, candle in bars.items():
            histories[coin].append(candle)

    final_equity = equity_curve[-1].equity
    summary = {
        "network": network,
        "coins": coins,
        "interval": interval,
        "strategy": strategy_name,
        "candles": len(aligned),
        "start_time_utc": equity_curve[0].time_utc,
        "end_time_utc": equity_curve[-1].time_utc,
        "initial_cash": round(initial_cash, 8),
        "final_equity": round(final_equity, 8),
        "return_pct": round((final_equity / initial_cash - 1) * 100, 8),
        "max_drawdown_pct": round(min(point.drawdown_pct for point in equity_curve), 8),
        "trades": len(trades),
        "total_fees": round(portfolio.total_fees, 8),
        "sharpe_per_bar": round(sharpe_per_bar(returns), 8),
    }
    return {
        "summary": summary,
        "equity_curve": [asdict(point) for point in equity_curve],
        "trades": [asdict(trade) for trade in trades],
    }


def normalize_targets(
    targets: dict[str, float],
    *,
    coins: list[str],
    allow_short: bool,
    max_gross_exposure: float,
    max_position_weight: float,
) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for coin in coins:
        value = float(targets.get(coin, 0.0))
        if not math.isfinite(value):
            value = 0.0
        if not allow_short:
            value = max(0.0, value)
        value = max(-max_position_weight, min(max_position_weight, value))
        normalized[coin] = value

    gross = sum(abs(value) for value in normalized.values())
    if gross > max_gross_exposure > 0:
        scale = max_gross_exposure / gross
        normalized = {coin: value * scale for coin, value in normalized.items()}
    return normalized


def sharpe_per_bar(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    return mean / std if std else 0.0


def write_backtest_outputs(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_equity_curve(output_dir / "equity_curve.csv", result["equity_curve"], result["summary"]["coins"])
    write_trades(output_dir / "trades.csv", result["trades"])


def write_equity_curve(path: Path, rows: list[dict[str, Any]], coins: list[str]) -> None:
    headers = ["time_utc", "time_ms", "equity", "cash", "drawdown_pct"]
    headers.extend(f"close_{coin}" for coin in coins)
    headers.extend(f"position_{coin}" for coin in coins)
    headers.extend(f"notional_{coin}" for coin in coins)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            flat = {
                "time_utc": row["time_utc"],
                "time_ms": row["time_ms"],
                "equity": row["equity"],
                "cash": row["cash"],
                "drawdown_pct": row["drawdown_pct"],
            }
            flat.update({f"close_{coin}": row["close_prices"].get(coin, 0.0) for coin in coins})
            flat.update({f"position_{coin}": row["positions"].get(coin, 0.0) for coin in coins})
            flat.update({f"notional_{coin}": row["notionals"].get(coin, 0.0) for coin in coins})
            writer.writerow(flat)


def write_trades(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = ["time_utc", "time_ms", "coin", "side", "quantity", "price", "notional", "fee", "cash_after"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_baseline(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stable_metrics(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare_baseline(path: Path, summary: dict[str, Any], *, tolerance_pct: float) -> tuple[bool, list[str]]:
    expected = json.loads(path.read_text(encoding="utf-8"))
    actual = stable_metrics(summary)
    messages: list[str] = []
    ok = True

    for key in ("network", "coins", "interval", "strategy", "candles"):
        if expected.get(key) != actual.get(key):
            ok = False
            messages.append(f"baseline mismatch {key}: expected {expected.get(key)!r}, actual {actual.get(key)!r}")

    for key in ("final_equity", "return_pct", "max_drawdown_pct", "total_fees", "sharpe_per_bar"):
        expected_value = float(expected.get(key, 0.0))
        actual_value = float(actual.get(key, 0.0))
        tolerance = abs(expected_value) * tolerance_pct / 100 if key == "final_equity" else tolerance_pct
        if abs(expected_value - actual_value) > tolerance:
            ok = False
            messages.append(f"baseline drift {key}: expected {expected_value}, actual {actual_value}")

    if int(expected.get("trades", 0)) != int(actual.get("trades", 0)):
        ok = False
        messages.append(f"baseline mismatch trades: expected {expected.get('trades')}, actual {actual.get('trades')}")

    if ok:
        messages.append("baseline check passed")
    return ok, messages


def stable_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "network",
        "coins",
        "interval",
        "strategy",
        "candles",
        "initial_cash",
        "final_equity",
        "return_pct",
        "max_drawdown_pct",
        "trades",
        "total_fees",
        "sharpe_per_bar",
    ]
    return {key: summary[key] for key in keys}


def print_summary(summary: dict[str, Any], output_dir: Path) -> None:
    log_info(f"output summary dir={format_path(output_dir)}")
    print(f"output: {output_dir}")
    print(f"coins: {','.join(summary['coins'])}")
    print(f"strategy: {summary['strategy']}")
    print(f"range: {summary['start_time_utc']} -> {summary['end_time_utc']}")
    print(f"candles: {summary['candles']}")
    print(f"final_equity: {summary['final_equity']}")
    print(f"return_pct: {summary['return_pct']}")
    print(f"max_drawdown_pct: {summary['max_drawdown_pct']}")
    print(f"trades: {summary['trades']}")


def default_output_dir(network: str, coins: list[str], interval: str, strategy: str) -> Path:
    safe_coins = "_".join(safe_name(coin) for coin in coins)
    return Path("reports") / "backtests" / f"{network}_{safe_coins}_{interval}_{safe_name(strategy)}"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value).strip("_") or "value"


def strategy_name(strategy: Strategy) -> str:
    return str(getattr(strategy, "name", strategy.__class__.__name__))


if __name__ == "__main__":
    raise SystemExit(main())

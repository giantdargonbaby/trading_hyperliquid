from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hyperliquid_trade_store.collector import HyperliquidCollector
from hyperliquid_trade_store.storage import (
    begin_run,
    connect,
    finish_run,
    get_state,
    init_db,
    insert_orderbook_snapshot,
    insert_public_snapshot,
    set_state,
    upsert_asset_contexts,
    upsert_market_candles,
    upsert_market_mids,
    upsert_market_trades,
)
from hyperliquid_trade_store.time_utils import parse_time_ms, utc_now_ms


INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    coin = args.coin.strip()
    if not coin:
        parser.error("--coin cannot be empty")

    end_time_ms = parse_time_ms(args.end) or utc_now_ms()
    start_time_ms = resolve_start_time(args, coin, end_time_ms)

    conn = connect(args.db)
    init_db(conn)

    run_id = begin_run(
        conn,
        network=args.network,
        target=f"public:{coin}",
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )

    try:
        collector = HyperliquidCollector(network=args.network, timeout=args.timeout, enable_ws=args.stream_trades)
        summary = collect_public_market_data(args, conn, collector, coin, start_time_ms, end_time_ms)
        finish_run(conn, run_id, status="success", message=str(summary))
    except Exception as error:  # noqa: BLE001 - CLI should record the failed sync run.
        finish_run(conn, run_id, status="failed", message=str(error))
        print(f"sync failed: {error}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print_summary(args.db, summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperliquid-trade-store",
        description="Fetch public Hyperliquid market data through the official Python SDK and store it in SQLite.",
    )
    parser.add_argument("--coin", required=True, help='Market symbol, for example "BTC", "ETH", or spot symbols like "PURR/USDC".')
    parser.add_argument("--db", default="data/hyperliquid.sqlite", type=Path, help="SQLite database path.")
    parser.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    parser.add_argument("--interval", choices=INTERVALS, default="1m", help="Candle interval.")
    parser.add_argument("--start", help="Start time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--end", help="End time. Accepts epoch ms, epoch seconds, or ISO-8601. Defaults to now.")
    parser.add_argument("--lookback-hours", type=float, default=24.0, help="Initial candle lookback when --start is omitted.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds.")
    parser.add_argument("--no-incremental", dest="incremental", action="store_false", help="Ignore saved candle cursor state.")
    parser.add_argument("--skip-candles", action="store_true", help="Skip candle snapshot collection.")
    parser.add_argument("--skip-book", action="store_true", help="Skip L2 order book snapshot collection.")
    parser.add_argument("--skip-contexts", action="store_true", help="Skip all mids and asset context snapshots.")
    parser.add_argument("--stream-trades", action="store_true", help="Subscribe to public realtime trades over websocket.")
    parser.add_argument("--duration-seconds", type=float, default=60.0, help="How long to stream trades when --stream-trades is set.")
    parser.set_defaults(incremental=True)
    return parser


def resolve_start_time(args: argparse.Namespace, coin: str, end_time_ms: int) -> int:
    explicit_start = parse_time_ms(args.start)
    if explicit_start is not None:
        return explicit_start

    if args.incremental:
        conn = connect(args.db)
        try:
            init_db(conn)
            state_key = candle_state_key(args.network, coin, args.interval)
            state_value = get_state(conn, state_key)
            if state_value is not None:
                return int(state_value) + 1
        finally:
            conn.close()

    lookback_ms = int(args.lookback_hours * 60 * 60 * 1000)
    return max(0, end_time_ms - lookback_ms)


def collect_public_market_data(
    args: argparse.Namespace,
    conn,
    collector: HyperliquidCollector,
    coin: str,
    start_time_ms: int,
    end_time_ms: int,
) -> dict[str, int]:
    summary = {
        "mids": 0,
        "asset_contexts": 0,
        "candles": 0,
        "orderbook_levels": 0,
        "public_snapshots": 0,
        "trades": 0,
    }
    captured_at_ms = utc_now_ms()

    if not args.skip_contexts:
        mids = collector.all_mids()
        summary["mids"] = upsert_market_mids(conn, network=args.network, mids=mids, captured_at_ms=captured_at_ms)
        insert_public_snapshot(conn, network=args.network, kind="all_mids", payload=mids, captured_at_ms=captured_at_ms)
        summary["public_snapshots"] += 1

        contexts = collector.asset_contexts()
        summary["asset_contexts"] = upsert_asset_contexts(
            conn,
            network=args.network,
            contexts=contexts,
            captured_at_ms=captured_at_ms,
        )
        insert_public_snapshot(
            conn,
            network=args.network,
            kind="asset_contexts",
            payload=contexts,
            captured_at_ms=captured_at_ms,
        )
        summary["public_snapshots"] += 1

    if not args.skip_candles:
        last_open_time = None
        for batch in collector.candle_batches(
            coin=coin,
            interval=args.interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            summary["candles"] += upsert_market_candles(conn, network=args.network, candles=batch)
            batch_last_open_time = max((int(candle["t"]) for candle in batch if candle.get("t") is not None), default=None)
            if batch_last_open_time is not None:
                last_open_time = batch_last_open_time if last_open_time is None else max(last_open_time, batch_last_open_time)
        if last_open_time is not None:
            set_state(conn, candle_state_key(args.network, coin, args.interval), str(last_open_time))

    if not args.skip_book:
        snapshot = collector.l2_book(coin=coin)
        summary["orderbook_levels"] = insert_orderbook_snapshot(
            conn,
            network=args.network,
            coin=coin,
            snapshot=snapshot,
        )

    if args.stream_trades:
        for batch in collector.stream_trade_batches(coin=coin, duration_seconds=args.duration_seconds):
            summary["trades"] += upsert_market_trades(conn, network=args.network, trades=batch)

    return summary


def candle_state_key(network: str, coin: str, interval: str) -> str:
    return f"{network}:public:{coin}:candles:{interval}:last_open_time_ms"


def print_summary(db_path: Path, summary: dict[str, int]) -> None:
    print(f"stored data in {db_path}")
    for key, value in summary.items():
        print(f"{key}: {value}")

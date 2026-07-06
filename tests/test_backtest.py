from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hyperliquid_trade_store.backtest import (
    BuyAndHoldStrategy,
    auto_fetch_market_candles,
    compare_baseline,
    load_aligned_candles,
    resolve_fetch_window,
    run_backtest,
    write_backtest_outputs,
    write_baseline,
)
from hyperliquid_trade_store.storage import init_db, upsert_market_candles


class BacktestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        upsert_market_candles(
            self.conn,
            network="mainnet",
            candles=[
                candle("BTC", 1_000, 100, 101),
                candle("BTC", 2_000, 101, 104),
                candle("BTC", 3_000, 104, 103),
                candle("ETH", 1_000, 50, 51),
                candle("ETH", 2_000, 51, 52),
                candle("ETH", 3_000, 52, 55),
            ],
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_load_aligned_candles_supports_multiple_coins(self) -> None:
        aligned = load_aligned_candles(
            self.conn,
            network="mainnet",
            coins=["BTC", "ETH"],
            interval="1m",
            start_time_ms=None,
            end_time_ms=None,
            max_candles=100,
        )

        self.assertEqual(len(aligned), 3)
        self.assertEqual(aligned[0][1]["BTC"].open, 100.0)
        self.assertEqual(aligned[0][1]["ETH"].close, 51.0)

    def test_buy_and_hold_backtest_writes_outputs(self) -> None:
        aligned = load_aligned_candles(
            self.conn,
            network="mainnet",
            coins=["BTC", "ETH"],
            interval="1m",
            start_time_ms=None,
            end_time_ms=None,
            max_candles=100,
        )
        result = run_backtest(
            aligned,
            coins=["BTC", "ETH"],
            strategy=BuyAndHoldStrategy(),
            initial_cash=10_000,
            fee_bps=0,
            slippage_bps=0,
            min_notional=0,
            allow_short=False,
            max_gross_exposure=1,
            max_position_weight=1,
            strategy_name="buy-and-hold",
            network="mainnet",
            interval="1m",
        )

        self.assertGreater(result["summary"]["final_equity"], 10_000)
        self.assertEqual(result["summary"]["trades"], 2)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            write_backtest_outputs(output_dir, result)
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "equity_curve.csv").exists())
            self.assertTrue((output_dir / "trades.csv").exists())

    def test_baseline_compare(self) -> None:
        aligned = load_aligned_candles(
            self.conn,
            network="mainnet",
            coins=["BTC"],
            interval="1m",
            start_time_ms=None,
            end_time_ms=None,
            max_candles=100,
        )
        result = run_backtest(
            aligned,
            coins=["BTC"],
            strategy=BuyAndHoldStrategy(),
            initial_cash=10_000,
            fee_bps=0,
            slippage_bps=0,
            min_notional=0,
            allow_short=False,
            max_gross_exposure=1,
            max_position_weight=1,
            strategy_name="buy-and-hold",
            network="mainnet",
            interval="1m",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline = Path(tmpdir) / "baseline.json"
            write_baseline(baseline, result["summary"])
            ok, messages = compare_baseline(baseline, result["summary"], tolerance_pct=0.01)

        self.assertTrue(ok)
        self.assertEqual(messages, ["baseline check passed"])

    def test_auto_fetch_market_candles_populates_empty_database(self) -> None:
        empty_conn = sqlite3.connect(":memory:")
        empty_conn.row_factory = sqlite3.Row
        init_db(empty_conn)
        try:
            fetched = auto_fetch_market_candles(
                empty_conn,
                network="mainnet",
                coins=["HYPE"],
                interval="1m",
                start_time_ms=1_000,
                end_time_ms=2_000,
                timeout=1,
                collector=FakeCollector(),
            )
            aligned = load_aligned_candles(
                empty_conn,
                network="mainnet",
                coins=["HYPE"],
                interval="1m",
                start_time_ms=None,
                end_time_ms=None,
                max_candles=100,
            )
        finally:
            empty_conn.close()

        self.assertEqual(fetched, {"HYPE": 2})
        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned[1][1]["HYPE"].close, 102.0)

    def test_resolve_fetch_window_uses_lookback_when_start_missing(self) -> None:
        start, end = resolve_fetch_window(start_time_ms=None, end_time_ms=3_600_000, lookback_hours=1)

        self.assertEqual((start, end), (0, 3_600_000))


def candle(coin: str, timestamp: int, open_price: float, close_price: float) -> dict:
    return {
        "t": timestamp,
        "T": timestamp + 59_999,
        "s": coin,
        "i": "1m",
        "o": str(open_price),
        "h": str(max(open_price, close_price) + 1),
        "l": str(min(open_price, close_price) - 1),
        "c": str(close_price),
        "v": "10",
        "n": 1,
    }


class FakeCollector:
    def candle_batches(self, *, coin, interval, start_time_ms, end_time_ms):
        yield [
            candle(coin, 1_000, 100, 101),
            candle(coin, 2_000, 101, 102),
        ]


if __name__ == "__main__":
    unittest.main()

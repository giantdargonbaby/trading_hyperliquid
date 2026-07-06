from __future__ import annotations

import sqlite3
import unittest

from hyperliquid_trade_store.storage import (
    init_db,
    insert_orderbook_snapshot,
    upsert_market_candles,
    upsert_market_mids,
    upsert_market_trades,
)
from hyperliquid_trade_store.time_utils import parse_time_ms


class PublicMarketStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_upsert_market_candles_is_idempotent(self) -> None:
        candle = {
            "t": 1783296000000,
            "T": 1783296059999,
            "s": "BTC",
            "i": "1m",
            "o": "100000",
            "h": "100100",
            "l": "99900",
            "c": "100050",
            "v": "12.5",
            "n": 42,
        }

        self.assertEqual(upsert_market_candles(self.conn, network="mainnet", candles=[candle]), 1)
        self.assertEqual(upsert_market_candles(self.conn, network="mainnet", candles=[candle]), 1)

        row = self.conn.execute("SELECT count(*) AS count, close, trades_count FROM market_candles").fetchone()
        self.assertEqual(dict(row), {"count": 1, "close": 100050.0, "trades_count": 42})

    def test_upsert_market_mids(self) -> None:
        count = upsert_market_mids(
            self.conn,
            network="mainnet",
            mids={"BTC": "100000", "ETH": "3000"},
            captured_at_ms=1783296000000,
        )

        self.assertEqual(count, 2)
        row = self.conn.execute("SELECT coin, mid FROM market_mids WHERE coin = 'ETH'").fetchone()
        self.assertEqual(dict(row), {"coin": "ETH", "mid": 3000.0})

    def test_insert_orderbook_snapshot_writes_levels(self) -> None:
        snapshot = {
            "coin": "BTC",
            "time": 1783296000000,
            "levels": [
                [{"px": "99999", "sz": "1.2", "n": 3}],
                [{"px": "100001", "sz": "0.8", "n": 2}],
            ],
        }

        level_count = insert_orderbook_snapshot(self.conn, network="mainnet", coin="BTC", snapshot=snapshot)

        self.assertEqual(level_count, 2)
        row = self.conn.execute("SELECT best_bid, best_ask FROM orderbook_snapshots").fetchone()
        self.assertEqual(dict(row), {"best_bid": 99999.0, "best_ask": 100001.0})

    def test_upsert_market_trades(self) -> None:
        trade = {
            "coin": "BTC",
            "side": "B",
            "px": "100000",
            "sz": "0.01",
            "hash": "0xabc",
            "time": 1783296000000,
            "tid": 123,
        }

        self.assertEqual(upsert_market_trades(self.conn, network="mainnet", trades=[trade]), 1)
        self.assertEqual(upsert_market_trades(self.conn, network="mainnet", trades=[trade]), 1)

        row = self.conn.execute("SELECT count(*) AS count, max(notional) AS notional FROM market_trades").fetchone()
        self.assertEqual(dict(row), {"count": 1, "notional": 1000.0})


class TimeUtilsTest(unittest.TestCase):
    def test_parse_time_ms(self) -> None:
        self.assertEqual(parse_time_ms("1783296000000"), 1783296000000)
        self.assertEqual(parse_time_ms("1783296000"), 1783296000000)
        self.assertEqual(parse_time_ms("2026-07-06T00:00:00Z"), 1783296000000)


if __name__ == "__main__":
    unittest.main()

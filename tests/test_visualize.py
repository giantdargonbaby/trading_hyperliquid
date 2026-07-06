from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hyperliquid_trade_store.storage import init_db, upsert_market_candles
from hyperliquid_trade_store.visualize import load_candles, write_player_html


class VisualizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        upsert_market_candles(
            self.conn,
            network="mainnet",
            candles=[
                {
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
                },
                {
                    "t": 1783296060000,
                    "T": 1783296119999,
                    "s": "BTC",
                    "i": "1m",
                    "o": "100050",
                    "h": "100200",
                    "l": "100000",
                    "c": "100180",
                    "v": "8.25",
                    "n": 31,
                },
            ],
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_load_candles_filters_and_orders_rows(self) -> None:
        candles = load_candles(
            self.conn,
            network="mainnet",
            coin="BTC",
            interval="1m",
            start_time_ms=1783296060000,
            end_time_ms=None,
            max_candles=100,
        )

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["open"], 100050.0)
        self.assertEqual(candles[0]["trades"], 31)

    def test_write_player_html_embeds_payload(self) -> None:
        candles = load_candles(
            self.conn,
            network="mainnet",
            coin="BTC",
            interval="1m",
            start_time_ms=None,
            end_time_ms=None,
            max_candles=100,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "player.html"
            write_player_html(
                output,
                network="mainnet",
                coin="BTC",
                interval="1m",
                start_time_ms=None,
                end_time_ms=None,
                candles=candles,
                initial_window=60,
            )

            html = output.read_text(encoding="utf-8")
            self.assertIn("BTC 1m K-line Playback", html)
            self.assertIn("const DATA =", html)
            self.assertIn('"candles":[', html)
            self.assertIn('"network":"mainnet"', html)


if __name__ == "__main__":
    unittest.main()

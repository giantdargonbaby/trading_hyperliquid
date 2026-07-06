from __future__ import annotations

import unittest

from hyperliquid_trade_store.backtest import Candle, Portfolio
from strategies.myquant_classic_strategies import (
    MyquantMovingAverageStrategy,
    MyquantRsiStrategy,
    MyquantTurtleStrategy,
)


class MyquantClassicStrategiesTest(unittest.TestCase):
    def test_ma_cross_generates_long_target(self) -> None:
        rows = [candle(close) for close in [10, 10, 10, 10, 10, 12]]
        strategy = MyquantMovingAverageStrategy(period=3, short_enabled=True)

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=0)

        self.assertEqual(target, {"HYPE": 1.0})

    def test_rsi_oversold_generates_long_target(self) -> None:
        rows = [candle(close) for close in [10, 9, 8, 7, 6, 5]]
        strategy = MyquantRsiStrategy(period=5, oversold=30, overbought=70)

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=0)

        self.assertEqual(target, {"HYPE": 1.0})

    def test_turtle_low_breakout_generates_short_when_enabled(self) -> None:
        rows = [
            candle(10, high=11, low=9),
            candle(10, high=10.5, low=9.2),
            candle(10, high=10.2, low=9.1),
            candle(8.9, high=9.3, low=8.8),
        ]
        strategy = MyquantTurtleStrategy(entry_period=3, exit_period=2, short_enabled=True)

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=0)

        self.assertEqual(target, {"HYPE": -1.0})


def candle(close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        coin="HYPE",
        time_ms=0,
        time_utc="2026-07-06T00:00:00+00:00",
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=10,
        trades_count=1,
    )


def portfolio() -> Portfolio:
    return Portfolio(cash=10_000, positions={"HYPE": 0.0})


if __name__ == "__main__":
    unittest.main()

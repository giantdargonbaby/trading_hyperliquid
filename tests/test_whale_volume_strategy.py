from __future__ import annotations

import unittest

from hyperliquid_trade_store.backtest import Candle, Portfolio
from strategies.whale_volume_strategy import WhaleVolumeStrategy


class WhaleVolumeStrategyTest(unittest.TestCase):
    def test_volume_spike_up_opens_long_twenty_percent(self) -> None:
        strategy = WhaleVolumeStrategy()
        rows = [
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=31, open_price=100, close_price=101),
        ]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)

        self.assertEqual(target, {"HYPE": 0.2})

    def test_volume_spike_down_opens_short_twenty_percent(self) -> None:
        strategy = WhaleVolumeStrategy()
        rows = [
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=10, open_price=100, close_price=100),
            candle(volume=31, open_price=101, close_price=100),
        ]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)

        self.assertEqual(target, {"HYPE": -0.2})

    def test_holds_when_whale_volume_has_not_dropped(self) -> None:
        strategy = WhaleVolumeStrategy()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
        rows = rows + [candle(volume=9, open_price=101, close_price=101.1, high=101.2, low=100.8)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6)

        self.assertEqual(target, {})

    def test_market_maker_breakout_reverses_after_whale_leaves(self) -> None:
        strategy = WhaleVolumeStrategy()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
        rows = rows + [candle(volume=1, open_price=101, close_price=100.8, high=101.0, low=100.6)]
        self.assertEqual(strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6), {})
        rows = rows + [candle(volume=10, open_price=100.8, close_price=101.3, high=101.4, low=100.7)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7)

        self.assertEqual(target, {"HYPE": -0.2})

    def test_market_maker_opposite_breakout_adds_with_whale_direction(self) -> None:
        strategy = WhaleVolumeStrategy()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
        rows = rows + [candle(volume=1, open_price=101, close_price=100.8, high=101.0, low=100.6)]
        self.assertEqual(strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6), {})
        rows = rows + [candle(volume=10, open_price=100.8, close_price=98.8, high=100.9, low=98.7)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7)

        self.assertEqual(target, {"HYPE": 0.4})

    def test_breakout_buffer_requires_larger_breakout(self) -> None:
        strategy = WhaleVolumeStrategy(breakout_buffer_pct=0.2)
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
        rows = rows + [candle(volume=1, open_price=101, close_price=100.8, high=101.0, low=100.6)]
        self.assertEqual(strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6), {})
        rows = rows + [candle(volume=10, open_price=100.8, close_price=101.1, high=101.2, low=100.7)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7)

        self.assertEqual(target, {})


def base_up_spike() -> list[Candle]:
    return [
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=31, open_price=100, close_price=101, high=101, low=99),
    ]


def candle(
    *,
    volume: float,
    open_price: float,
    close_price: float,
    high: float | None = None,
    low: float | None = None,
) -> Candle:
    return Candle(
        coin="HYPE",
        time_ms=0,
        time_utc="2026-07-06T00:00:00+00:00",
        open=open_price,
        high=high if high is not None else max(open_price, close_price),
        low=low if low is not None else min(open_price, close_price),
        close=close_price,
        volume=volume,
        trades_count=1,
    )


def portfolio() -> Portfolio:
    return Portfolio(cash=10_000, positions={"HYPE": 0.0})


if __name__ == "__main__":
    unittest.main()

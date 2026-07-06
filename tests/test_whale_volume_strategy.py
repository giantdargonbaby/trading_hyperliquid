from __future__ import annotations

import unittest

from hyperliquid_trade_store.backtest import Candle, Portfolio
from strategies.whale_volume_strategy import WhaleMarketMakerStrategyV2, WhaleVolumeStrategy


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


class WhaleMarketMakerStrategyV2Test(unittest.TestCase):
    def test_whale_entry_only_starts_watching_without_position(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = base_up_spike()

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)

        self.assertEqual(target, {})

    def test_high_volume_non_higher_close_is_whale_conflict_and_does_not_trade(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
        rows = rows + [candle(volume=20, open_price=101, close_price=100.9)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6)

        self.assertEqual(target, {})

    def test_three_low_volume_rising_bars_opens_reverse_short_five_percent(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)

        rows = rows + [candle(volume=10, open_price=101, close_price=101.2)]
        self.assertEqual(strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6), {})
        rows = rows + [candle(volume=10, open_price=101.2, close_price=101.4)]
        self.assertEqual(strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7), {})
        rows = rows + [candle(volume=10, open_price=101.4, close_price=101.6)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=8)

        self.assertEqual(target, {"HYPE": -0.05})

    def test_continued_rise_adds_short_until_thirty_percent(self) -> None:
        strategy = WhaleMarketMakerStrategyV2(
            cooldown_bars=0,
            max_trades_per_day=20,
            max_hold_bars=0,
            stop_loss_pct=0,
            take_profit_pct=0,
        )
        rows = enter_v2_market_maker_short(strategy)

        targets = []
        for index in range(5):
            last_close = rows[-1].close
            rows = rows + [candle(volume=10, open_price=last_close, close_price=last_close + 0.2)]
            target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=9 + index)
            targets.append(target["HYPE"])

        self.assertAlmostEqual(targets[-1], -0.3)

        rows = rows + [candle(volume=10, open_price=rows[-1].close, close_price=rows[-1].close + 0.2)]
        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=20)

        self.assertEqual(target, {})

    def test_default_cooldown_blocks_immediate_add(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = enter_v2_market_maker_short(strategy)
        rows = rows + [candle(volume=10, open_price=101.6, close_price=101.8)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=9)

        self.assertEqual(target, {})

    def test_stop_loss_closes_even_during_cooldown(self) -> None:
        strategy = WhaleMarketMakerStrategyV2(cooldown_bars=10, stop_loss_pct=0.1, take_profit_pct=0, max_hold_bars=0)
        rows = enter_v2_market_maker_short(strategy)
        rows = rows + [candle(volume=10, open_price=101.6, close_price=102.0)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=9)

        self.assertEqual(target, {"HYPE": 0.0})

    def test_rise_break_stops_adding_short(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = enter_v2_market_maker_short(strategy)
        rows = rows + [candle(volume=10, open_price=101.6, close_price=101.3)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=9)

        self.assertEqual(target, {})

    def test_three_low_volume_falling_bars_opens_reverse_long_five_percent(self) -> None:
        strategy = WhaleMarketMakerStrategyV2()
        rows = base_up_spike()
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)

        rows = rows + [candle(volume=10, open_price=101, close_price=100.8)]
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6)
        rows = rows + [candle(volume=10, open_price=100.8, close_price=100.6)]
        strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7)
        rows = rows + [candle(volume=10, open_price=100.6, close_price=100.4)]

        target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=8)

        self.assertEqual(target, {"HYPE": 0.05})


def base_up_spike() -> list[Candle]:
    return [
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=10, open_price=100, close_price=100),
        candle(volume=31, open_price=100, close_price=101, high=101, low=99),
    ]


def enter_v2_market_maker_short(strategy: WhaleMarketMakerStrategyV2) -> list[Candle]:
    rows = base_up_spike()
    strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=5)
    rows = rows + [candle(volume=10, open_price=101, close_price=101.2)]
    strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=6)
    rows = rows + [candle(volume=10, open_price=101.2, close_price=101.4)]
    strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=7)
    rows = rows + [candle(volume=10, open_price=101.4, close_price=101.6)]
    target = strategy.generate_targets(histories={"HYPE": rows}, portfolio=portfolio(), timestamp_ms=8)
    assert target == {"HYPE": -0.05}
    return rows


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

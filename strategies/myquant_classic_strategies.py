from __future__ import annotations

import math
from statistics import pstdev


class MyquantMovingAverageStrategy:
    """MA cross strategy adapted from myquant/strategy MA examples."""

    name = "myquant-ma-cross"

    def __init__(
        self,
        period: int = 20,
        target_weight: float = 1.0,
        short_enabled: bool = True,
        min_delta_pct: float = 0.0,
    ) -> None:
        self.period = require_positive_int(period, "period")
        self.target_weight = require_positive_float(target_weight, "target_weight")
        self.short_enabled = bool(short_enabled)
        self.min_delta = max(0.0, float(min_delta_pct)) / 100

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        for coin, rows in histories.items():
            if len(rows) < self.period + 2:
                continue
            closes = [row.close for row in rows]
            prev_ma = mean(closes[-self.period - 1 : -1])
            last_ma = mean(closes[-self.period :])
            prev_delta = closes[-2] - prev_ma
            last_delta = closes[-1] - last_ma
            if crosses_up(prev_delta, last_delta) and abs(last_delta / last_ma) >= self.min_delta:
                targets[coin] = self.target_weight
            elif crosses_down(prev_delta, last_delta) and abs(last_delta / last_ma) >= self.min_delta:
                targets[coin] = -self.target_weight if self.short_enabled else 0.0
        return targets


class MyquantMacdStrategy:
    """MACD trend strategy adapted from myquant/strategy MACD-STOCK."""

    name = "myquant-macd"

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        target_weight: float = 1.0,
        short_enabled: bool = True,
    ) -> None:
        self.fast_period = require_positive_int(fast_period, "fast_period")
        self.slow_period = require_positive_int(slow_period, "slow_period")
        self.signal_period = require_positive_int(signal_period, "signal_period")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        self.target_weight = require_positive_float(target_weight, "target_weight")
        self.short_enabled = bool(short_enabled)

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        min_rows = self.slow_period + self.signal_period + 2
        for coin, rows in histories.items():
            if len(rows) < min_rows:
                continue
            closes = [row.close for row in rows]
            dif, dea = macd(closes, self.fast_period, self.slow_period, self.signal_period)
            if len(dif) < 2 or len(dea) < 2:
                continue
            bullish = dif[-1] > 0 and dea[-1] > 0 and dif[-1] > dif[-2] and dif[-1] > dea[-1]
            bearish = dif[-1] < 0 and dea[-1] < 0 and dif[-1] < dif[-2] and dif[-1] < dea[-1]
            if bullish:
                targets[coin] = self.target_weight
            elif bearish:
                targets[coin] = -self.target_weight if self.short_enabled else 0.0
        return targets


class MyquantRsiStrategy:
    """RSI mean-reversion strategy adapted from myquant/strategy RSI_STOCK."""

    name = "myquant-rsi"

    def __init__(
        self,
        period: int = 14,
        overbought: float = 70.0,
        oversold: float = 30.0,
        target_weight: float = 1.0,
        short_enabled: bool = False,
    ) -> None:
        self.period = require_positive_int(period, "period")
        self.overbought = float(overbought)
        self.oversold = float(oversold)
        if self.oversold >= self.overbought:
            raise ValueError("oversold must be less than overbought")
        self.target_weight = require_positive_float(target_weight, "target_weight")
        self.short_enabled = bool(short_enabled)

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        for coin, rows in histories.items():
            if len(rows) < self.period + 1:
                continue
            value = rsi([row.close for row in rows], self.period)
            if value is None:
                continue
            if value < self.oversold:
                targets[coin] = self.target_weight
            elif value > self.overbought:
                targets[coin] = -self.target_weight if self.short_enabled else 0.0
        return targets


class MyquantBollTrendStrategy:
    """Bollinger trend strategy adapted from myquant/strategy BOLL_STOCK."""

    name = "myquant-boll-trend"

    def __init__(
        self,
        period: int = 20,
        deviation: float = 2.0,
        target_weight: float = 1.0,
        short_enabled: bool = True,
    ) -> None:
        self.period = require_positive_int(period, "period")
        self.deviation = require_positive_float(deviation, "deviation")
        self.target_weight = require_positive_float(target_weight, "target_weight")
        self.short_enabled = bool(short_enabled)

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        for coin, rows in histories.items():
            if len(rows) < self.period + 2:
                continue
            closes = [row.close for row in rows]
            bands = [bollinger(closes[: index], self.period, self.deviation) for index in range(len(closes) - 2, len(closes) + 1)]
            if any(band is None for band in bands):
                continue
            upper_2, middle_2, lower_2 = bands[0]
            upper_1, middle_1, lower_1 = bands[1]
            upper, middle, lower = bands[2]
            rising = upper > upper_1 > upper_2 and middle > middle_1 > middle_2 and lower > lower_1 > lower_2
            falling = upper < upper_1 and middle < middle_1 and lower < lower_1
            if rising and closes[-1] > middle:
                targets[coin] = self.target_weight
            elif falling or closes[-1] < middle:
                targets[coin] = -self.target_weight if self.short_enabled else 0.0
        return targets


class MyquantTurtleStrategy:
    """Turtle breakout strategy adapted from myquant/strategy Turtle."""

    name = "myquant-turtle"

    def __init__(
        self,
        entry_period: int = 20,
        exit_period: int = 10,
        target_weight: float = 1.0,
        short_enabled: bool = True,
    ) -> None:
        self.entry_period = require_positive_int(entry_period, "entry_period")
        self.exit_period = require_positive_int(exit_period, "exit_period")
        self.target_weight = require_positive_float(target_weight, "target_weight")
        self.short_enabled = bool(short_enabled)

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        required = max(self.entry_period, self.exit_period) + 1
        for coin, rows in histories.items():
            if len(rows) < required:
                continue
            signal = rows[-1]
            entry_window = rows[-self.entry_period - 1 : -1]
            exit_window = rows[-self.exit_period - 1 : -1]
            entry_high = max(row.high for row in entry_window)
            entry_low = min(row.low for row in entry_window)
            exit_high = max(row.high for row in exit_window)
            exit_low = min(row.low for row in exit_window)
            current_units = portfolio.positions.get(coin, 0.0)
            if signal.close > entry_high:
                targets[coin] = self.target_weight
            elif signal.close < entry_low:
                targets[coin] = -self.target_weight if self.short_enabled else 0.0
            elif current_units > 0 and signal.close < exit_low:
                targets[coin] = 0.0
            elif current_units < 0 and signal.close > exit_high:
                targets[coin] = 0.0
        return targets


def require_positive_int(value: int, name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def require_positive_float(value: float, name: str) -> float:
    parsed = float(value)
    if parsed <= 0 or not math.isfinite(parsed):
        raise ValueError(f"{name} must be positive")
    return parsed


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def crosses_up(previous: float, current: float) -> bool:
    return previous <= 0 < current


def crosses_down(previous: float, current: float) -> bool:
    return previous >= 0 > current


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * alpha + result[-1] * (1 - alpha))
    return result


def macd(closes: list[float], fast_period: int, slow_period: int, signal_period: int) -> tuple[list[float], list[float]]:
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    dea = ema(dif, signal_period)
    return dif, dea


def rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def bollinger(closes: list[float], period: int, deviation: float) -> tuple[float, float, float] | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = mean(window)
    std = pstdev(window)
    upper = middle + deviation * std
    lower = middle - deviation * std
    return upper, middle, lower

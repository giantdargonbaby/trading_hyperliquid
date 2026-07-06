from __future__ import annotations


class WhaleVolumeStrategy:
    name = "whale-volume-follow"

    def __init__(
        self,
        volume_lookback: int = 3,
        spike_multiplier: float = 3.0,
        volume_drop_ratio: float = 0.5,
        pullback_tolerance_pct: float = 0.5,
        breakout_buffer_pct: float = 0.0,
        step_weight: float = 0.2,
        max_abs_weight: float = 1.0,
    ) -> None:
        if volume_lookback <= 0:
            raise ValueError("volume_lookback must be positive")
        if spike_multiplier <= 0:
            raise ValueError("spike_multiplier must be positive")
        if volume_drop_ratio <= 0:
            raise ValueError("volume_drop_ratio must be positive")
        if pullback_tolerance_pct < 0:
            raise ValueError("pullback_tolerance_pct must be non-negative")
        if breakout_buffer_pct < 0:
            raise ValueError("breakout_buffer_pct must be non-negative")
        if step_weight <= 0:
            raise ValueError("step_weight must be positive")
        if max_abs_weight <= 0:
            raise ValueError("max_abs_weight must be positive")

        self.volume_lookback = volume_lookback
        self.spike_multiplier = spike_multiplier
        self.volume_drop_ratio = volume_drop_ratio
        self.pullback_tolerance = pullback_tolerance_pct / 100
        self.breakout_buffer = breakout_buffer_pct / 100
        self.step_weight = step_weight
        self.max_abs_weight = max_abs_weight
        self.states = {}

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        for coin, rows in histories.items():
            if coin not in self.states:
                self.states[coin] = _CoinState()
            state = self.states[coin]
            target = self._target_for_coin(rows, state)
            if target is not None:
                targets[coin] = target
        return targets

    def _target_for_coin(self, rows, state):
        min_rows = self.volume_lookback + 1
        if len(rows) < min_rows:
            return None

        signal = rows[-1]
        previous = rows[-min_rows:-1]
        avg_volume = sum(row.volume for row in previous) / self.volume_lookback
        if avg_volume <= 0:
            return None

        whale_direction = self._whale_spike_direction(signal, avg_volume)
        if whale_direction != 0:
            state.start(whale_direction, signal)
            state.target_weight = self._clamp(state.target_weight + whale_direction * self.step_weight)
            return state.target_weight

        if state.phase == "watching_whale":
            self._update_extremes(state, signal)
            if signal.volume >= avg_volume * self.volume_drop_ratio:
                return None

            if self._within_pullback_tolerance(state, signal):
                state.phase = "market_maker"
                return None

            state.reset_session()
            return None

        if state.phase == "market_maker":
            action = self._market_maker_action(state, signal)
            if action == "reverse":
                state.target_weight = -state.whale_direction * self.step_weight
                state.reset_session(keep_target=True)
                return state.target_weight
            if action == "add_with_whale":
                state.target_weight = self._clamp(state.target_weight + state.whale_direction * self.step_weight)
                state.reset_session(keep_target=True)
                return state.target_weight

        return None

    def _whale_spike_direction(self, candle, avg_volume: float) -> int:
        if candle.volume <= avg_volume * self.spike_multiplier:
            return 0
        if candle.close > candle.open:
            return 1
        if candle.close < candle.open:
            return -1
        return 0

    def _update_extremes(self, state, candle) -> None:
        if state.phase == "idle":
            return
        state.high_since_entry = max(state.high_since_entry, candle.high)
        state.low_since_entry = min(state.low_since_entry, candle.low)

    def _within_pullback_tolerance(self, state, candle) -> bool:
        if state.whale_direction > 0:
            return candle.close >= state.high_since_entry * (1 - self.pullback_tolerance)
        return candle.close <= state.low_since_entry * (1 + self.pullback_tolerance)

    def _market_maker_action(self, state, candle) -> str | None:
        if state.whale_direction > 0:
            if candle.close > state.high_since_entry * (1 + self.breakout_buffer):
                return "reverse"
            if candle.close < state.low_since_entry * (1 - self.breakout_buffer):
                return "add_with_whale"
        else:
            if candle.close < state.low_since_entry * (1 - self.breakout_buffer):
                return "reverse"
            if candle.close > state.high_since_entry * (1 + self.breakout_buffer):
                return "add_with_whale"
        return None

    def _clamp(self, value: float) -> float:
        return max(-self.max_abs_weight, min(self.max_abs_weight, value))


class _CoinState:
    def __init__(self) -> None:
        self.target_weight = 0.0
        self.phase = "idle"
        self.whale_direction = 0
        self.high_since_entry = 0.0
        self.low_since_entry = 0.0

    def start(self, whale_direction: int, candle) -> None:
        self.phase = "watching_whale"
        self.whale_direction = whale_direction
        self.high_since_entry = candle.high
        self.low_since_entry = candle.low

    def reset_session(self, *, keep_target: bool = False) -> None:
        target_weight = self.target_weight
        self.phase = "idle"
        self.whale_direction = 0
        self.high_since_entry = 0.0
        self.low_since_entry = 0.0
        if keep_target:
            self.target_weight = target_weight

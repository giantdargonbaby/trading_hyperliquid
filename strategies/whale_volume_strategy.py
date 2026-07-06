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


class WhaleMarketMakerStrategyV2:
    name = "whale-market-maker-v2"

    def __init__(
        self,
        volume_lookback: int = 3,
        spike_multiplier: float = 3.0,
        exit_volume_ratio: float = 0.5,
        exit_bars: int = 3,
        step_weight: float = 0.05,
        max_abs_weight: float = 0.3,
        min_price_move_pct: float = 0.05,
        cooldown_bars: int = 5,
        max_hold_bars: int = 120,
        stop_loss_pct: float = 0.4,
        take_profit_pct: float = 0.6,
        max_trades_per_day: int = 80,
    ) -> None:
        if volume_lookback <= 0:
            raise ValueError("volume_lookback must be positive")
        if spike_multiplier <= 0:
            raise ValueError("spike_multiplier must be positive")
        if exit_volume_ratio <= 0:
            raise ValueError("exit_volume_ratio must be positive")
        if exit_bars <= 0:
            raise ValueError("exit_bars must be positive")
        if step_weight <= 0:
            raise ValueError("step_weight must be positive")
        if max_abs_weight <= 0:
            raise ValueError("max_abs_weight must be positive")
        if min_price_move_pct < 0:
            raise ValueError("min_price_move_pct must be non-negative")
        if cooldown_bars < 0:
            raise ValueError("cooldown_bars must be non-negative")
        if max_hold_bars < 0:
            raise ValueError("max_hold_bars must be non-negative")
        if stop_loss_pct < 0:
            raise ValueError("stop_loss_pct must be non-negative")
        if take_profit_pct < 0:
            raise ValueError("take_profit_pct must be non-negative")
        if max_trades_per_day <= 0:
            raise ValueError("max_trades_per_day must be positive")

        self.volume_lookback = volume_lookback
        self.spike_multiplier = spike_multiplier
        self.exit_volume_ratio = exit_volume_ratio
        self.exit_bars = exit_bars
        self.step_weight = step_weight
        self.max_abs_weight = max_abs_weight
        self.min_price_move = min_price_move_pct / 100
        self.cooldown_bars = cooldown_bars
        self.max_hold_bars = max_hold_bars
        self.stop_loss = stop_loss_pct / 100
        self.take_profit = take_profit_pct / 100
        self.max_trades_per_day = max_trades_per_day
        self.states = {}

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        targets = {}
        for coin, rows in histories.items():
            if coin not in self.states:
                self.states[coin] = _V2CoinState()
            state = self.states[coin]
            target = self._target_for_coin(rows, state)
            if target is not None:
                targets[coin] = target
        return targets

    def _target_for_coin(self, rows, state):
        min_rows = self.volume_lookback + 1
        if len(rows) < min_rows:
            return None

        risk_target = self._risk_exit_target(rows, state)
        if risk_target is not None:
            return risk_target

        signal = rows[-1]
        previous = rows[-min_rows:-1]
        avg_volume = sum(row.volume for row in previous) / self.volume_lookback
        if avg_volume <= 0:
            return None

        if signal.volume > avg_volume * self.spike_multiplier:
            state.start(signal)
            return None

        if state.phase in {"watching_whale", "whale_conflict"}:
            return self._watch_whale_exit(rows, state)

        if state.phase == "market_maker":
            return self._market_maker_target(rows, state)

        return None

    def _watch_whale_exit(self, rows, state):
        signal = rows[-1]
        previous_close = rows[-2].close
        exit_volume = state.entry_volume * self.exit_volume_ratio

        if signal.volume >= exit_volume:
            state.low_volume_streak = 0
            if signal.close <= previous_close:
                state.phase = "whale_conflict"
            return None

        state.low_volume_streak += 1
        if state.low_volume_streak < self.exit_bars:
            return None

        state.phase = "market_maker"
        return self._market_maker_target(rows, state)

    def _market_maker_target(self, rows, state):
        signal = rows[-1]
        previous_close = rows[-2].close
        price_direction = self._price_direction(signal.close, previous_close)
        if price_direction == 0:
            return None
        if previous_close <= 0:
            return None
        if abs(signal.close / previous_close - 1) < self.min_price_move:
            return None

        if state.maker_price_direction == 0:
            state.maker_price_direction = price_direction
        elif price_direction != state.maker_price_direction:
            return None

        next_target = self._clamp(state.target_weight - price_direction * self.step_weight)
        if next_target == state.target_weight:
            return None

        return self._set_target(rows, state, next_target)

    def _risk_exit_target(self, rows, state):
        if state.target_weight == 0 or state.position_entry_price <= 0:
            return None

        signal = rows[-1]
        bar_index = len(rows) - 1
        if self.max_hold_bars and state.position_entry_bar_index is not None:
            if bar_index - state.position_entry_bar_index >= self.max_hold_bars:
                return self._set_target(rows, state, 0.0, force=True, reset_session=True)

        direction = 1 if state.target_weight > 0 else -1
        pnl = direction * (signal.close / state.position_entry_price - 1)
        if self.stop_loss and pnl <= -self.stop_loss:
            return self._set_target(rows, state, 0.0, force=True, reset_session=True)
        if self.take_profit and pnl >= self.take_profit:
            return self._set_target(rows, state, 0.0, force=True, reset_session=True)
        return None

    def _set_target(self, rows, state, target_weight: float, *, force: bool = False, reset_session: bool = False):
        target_weight = self._clamp(target_weight)
        if target_weight == state.target_weight:
            return None
        if not force and not self._can_trade(rows, state):
            return None

        old_weight = state.target_weight
        state.target_weight = target_weight
        state.mark_trade(rows[-1], len(rows) - 1, old_weight=old_weight, new_weight=target_weight)
        if reset_session:
            state.reset_session(keep_target=True)
        return state.target_weight

    def _can_trade(self, rows, state) -> bool:
        bar_index = len(rows) - 1
        state.prepare_trade_day(rows[-1])
        if state.trades_today >= self.max_trades_per_day:
            return False
        if state.last_trade_bar_index is None:
            return True
        return bar_index - state.last_trade_bar_index > self.cooldown_bars

    def _price_direction(self, close: float, previous_close: float) -> int:
        if close > previous_close:
            return 1
        if close < previous_close:
            return -1
        return 0

    def _clamp(self, value: float) -> float:
        return max(-self.max_abs_weight, min(self.max_abs_weight, value))


class _V2CoinState:
    def __init__(self) -> None:
        self.target_weight = 0.0
        self.phase = "idle"
        self.entry_volume = 0.0
        self.low_volume_streak = 0
        self.maker_price_direction = 0
        self.position_entry_price = 0.0
        self.position_entry_bar_index = None
        self.last_trade_bar_index = None
        self.trade_day = None
        self.trades_today = 0

    def start(self, candle) -> None:
        self.phase = "watching_whale"
        self.entry_volume = candle.volume
        self.low_volume_streak = 0
        self.maker_price_direction = 0

    def prepare_trade_day(self, candle) -> None:
        day = str(candle.time_utc)[:10]
        if day != self.trade_day:
            self.trade_day = day
            self.trades_today = 0

    def mark_trade(self, candle, bar_index: int, *, old_weight: float, new_weight: float) -> None:
        self.prepare_trade_day(candle)
        self.trades_today += 1
        self.last_trade_bar_index = bar_index

        if new_weight == 0:
            self.position_entry_price = 0.0
            self.position_entry_bar_index = None
            return

        if old_weight == 0 or (old_weight > 0) != (new_weight > 0):
            self.position_entry_price = candle.close
            self.position_entry_bar_index = bar_index
            return

        if abs(new_weight) > abs(old_weight):
            added_weight = abs(new_weight) - abs(old_weight)
            self.position_entry_price = (
                self.position_entry_price * abs(old_weight) + candle.close * added_weight
            ) / abs(new_weight)

    def reset_session(self, *, keep_target: bool = False) -> None:
        target_weight = self.target_weight
        self.phase = "idle"
        self.entry_volume = 0.0
        self.low_volume_streak = 0
        self.maker_price_direction = 0
        if keep_target:
            self.target_weight = target_weight

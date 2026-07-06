from __future__ import annotations


class ExampleStrategy:
    name = "example-three-candle-momentum"

    def __init__(self, lookback: int = 3) -> None:
        self.lookback = lookback

    def generate_targets(self, *, histories, portfolio, timestamp_ms):
        active = []
        for coin, rows in histories.items():
            if len(rows) < self.lookback:
                continue
            if rows[-1].close > rows[-self.lookback].close:
                active.append(coin)

        if not active:
            return {coin: 0.0 for coin in histories}

        weight = 1.0 / len(active)
        return {coin: (weight if coin in active else 0.0) for coin in histories}

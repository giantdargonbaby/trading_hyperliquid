from __future__ import annotations

import time
from queue import Empty, Queue
from typing import Any


class HyperliquidCollector:
    def __init__(
        self,
        *,
        network: str = "mainnet",
        timeout: float | None = 20.0,
        enable_ws: bool = False,
    ) -> None:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        if network == "mainnet":
            base_url = constants.MAINNET_API_URL
        elif network == "testnet":
            base_url = constants.TESTNET_API_URL
        else:
            raise ValueError("network must be mainnet or testnet")

        self.network = network
        self.info = Info(base_url, skip_ws=not enable_ws, timeout=timeout)

    def all_mids(self) -> dict[str, Any]:
        return dict(self.info.all_mids())

    def candles(self, *, coin: str, interval: str, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]:
        return list(self.info.candles_snapshot(coin, interval, start_time_ms, end_time_ms))

    def candle_batches(
        self,
        *,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        max_candles_per_request: int = 4500,
    ) -> Any:
        interval_ms = interval_to_ms(interval)
        cursor = start_time_ms
        while cursor <= end_time_ms:
            page_end = min(end_time_ms, cursor + interval_ms * max_candles_per_request - 1)
            batch = self.candles(coin=coin, interval=interval, start_time_ms=cursor, end_time_ms=page_end)
            if batch:
                batch.sort(key=lambda item: int(item.get("t", 0)))
                yield batch
                last_time = max(int(item.get("t", 0)) for item in batch if item.get("t") is not None)
                cursor = max(page_end + 1, last_time + interval_ms)
            else:
                cursor = page_end + 1

    def l2_book(self, *, coin: str) -> dict[str, Any]:
        return dict(self.info.l2_snapshot(coin))

    def asset_contexts(self) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []

        perp_meta, perp_contexts = self.info.meta_and_asset_ctxs()
        for asset, ctx in zip(perp_meta.get("universe", []), perp_contexts, strict=False):
            contexts.append(
                {
                    "kind": "perp",
                    "coin": asset.get("name"),
                    "asset": asset,
                    "ctx": ctx,
                }
            )

        spot_meta, spot_contexts = self.info.spot_meta_and_asset_ctxs()
        for asset, ctx in zip(spot_meta.get("universe", []), spot_contexts, strict=False):
            contexts.append(
                {
                    "kind": "spot",
                    "coin": ctx.get("coin") or asset.get("name"),
                    "asset": asset,
                    "ctx": ctx,
                }
            )

        return contexts

    def stream_trade_batches(self, *, coin: str, duration_seconds: float) -> Any:
        if self.info.ws_manager is None:
            raise RuntimeError("stream_trade_batches requires enable_ws=True")

        trade_batches: Queue[list[dict[str, Any]]] = Queue()
        subscription = {"type": "trades", "coin": coin}

        def callback(message: dict[str, Any]) -> None:
            data = message.get("data", message)
            if isinstance(data, list):
                trade_batches.put([trade for trade in data if isinstance(trade, dict)])

        subscription_id = self.info.subscribe(subscription, callback)
        deadline = time.monotonic() + max(0.0, duration_seconds)

        try:
            while time.monotonic() < deadline:
                timeout = min(0.5, max(0.0, deadline - time.monotonic()))
                try:
                    batch = trade_batches.get(timeout=timeout)
                except Empty:
                    continue
                if batch:
                    yield batch

            while True:
                try:
                    batch = trade_batches.get_nowait()
                except Empty:
                    break
                if batch:
                    yield batch
        finally:
            self.info.unsubscribe(subscription, subscription_id)
            self.info.disconnect_websocket()


def interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    if unit == "m":
        return value * 60 * 1000
    if unit == "h":
        return value * 60 * 60 * 1000
    if unit == "d":
        return value * 24 * 60 * 60 * 1000
    if unit == "w":
        return value * 7 * 24 * 60 * 60 * 1000
    if interval == "1M":
        return 30 * 24 * 60 * 60 * 1000
    raise ValueError(f"unsupported interval: {interval}")

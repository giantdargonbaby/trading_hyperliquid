from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hyperliquid_trade_store.time_utils import ms_to_utc_iso, utc_now_ms


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER,
    network TEXT NOT NULL,
    target TEXT NOT NULL,
    start_time_ms INTEGER,
    end_time_ms INTEGER,
    status TEXT NOT NULL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_mids (
    mid_key TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    coin TEXT NOT NULL,
    mid REAL,
    captured_at_ms INTEGER NOT NULL,
    captured_at_utc TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_mids_coin_time
ON market_mids(network, coin, captured_at_ms);

CREATE TABLE IF NOT EXISTS market_candles (
    candle_key TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    coin TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time_ms INTEGER NOT NULL,
    close_time_ms INTEGER,
    open_time_utc TEXT NOT NULL,
    close_time_utc TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    trades_count INTEGER,
    raw_json TEXT NOT NULL,
    inserted_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_candles_unique
ON market_candles(network, coin, interval, open_time_ms);

CREATE TABLE IF NOT EXISTS market_trades (
    trade_key TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    coin TEXT NOT NULL,
    time_ms INTEGER NOT NULL,
    time_utc TEXT NOT NULL,
    side TEXT,
    price REAL,
    size REAL,
    notional REAL,
    trade_hash TEXT,
    trade_id TEXT,
    raw_json TEXT NOT NULL,
    inserted_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_trades_coin_time
ON market_trades(network, coin, time_ms);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    coin TEXT NOT NULL,
    time_ms INTEGER NOT NULL,
    time_utc TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    raw_json TEXT NOT NULL,
    inserted_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_coin_time
ON orderbook_snapshots(network, coin, time_ms);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    snapshot_id TEXT NOT NULL,
    side TEXT NOT NULL,
    level_index INTEGER NOT NULL,
    price REAL,
    size REAL,
    orders_count INTEGER,
    PRIMARY KEY (snapshot_id, side, level_index),
    FOREIGN KEY (snapshot_id) REFERENCES orderbook_snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS asset_contexts (
    context_key TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    kind TEXT NOT NULL,
    coin TEXT NOT NULL,
    captured_at_ms INTEGER NOT NULL,
    captured_at_utc TEXT NOT NULL,
    mark_price REAL,
    mid_price REAL,
    oracle_price REAL,
    previous_day_price REAL,
    funding REAL,
    open_interest REAL,
    day_notional_volume REAL,
    circulating_supply REAL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_asset_contexts_coin_time
ON asset_contexts(network, kind, coin, captured_at_ms);

CREATE TABLE IF NOT EXISTS public_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    kind TEXT NOT NULL,
    captured_at_ms INTEGER NOT NULL,
    captured_at_utc TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def begin_run(
    conn: sqlite3.Connection,
    *,
    network: str,
    target: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO sync_runs (
            started_at_ms, network, target, start_time_ms, end_time_ms, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (utc_now_ms(), network, target, start_time_ms, end_time_ms, "running"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, *, status: str, message: str | None = None) -> None:
    conn.execute(
        """
        UPDATE sync_runs
        SET finished_at_ms = ?, status = ?, message = ?
        WHERE id = ?
        """,
        (utc_now_ms(), status, message, run_id),
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state(key, value, updated_at_ms)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at_ms = excluded.updated_at_ms
        """,
        (key, value, utc_now_ms()),
    )
    conn.commit()


def insert_public_snapshot(
    conn: sqlite3.Connection,
    *,
    network: str,
    kind: str,
    payload: Any,
    captured_at_ms: int | None = None,
) -> None:
    captured_at_ms = captured_at_ms or utc_now_ms()
    raw_json = _json(payload)
    snapshot_id = _sha256([network, kind, str(captured_at_ms), raw_json])
    conn.execute(
        """
        INSERT OR IGNORE INTO public_snapshots (
            snapshot_id, network, kind, captured_at_ms, captured_at_utc, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (snapshot_id, network, kind, captured_at_ms, ms_to_utc_iso(captured_at_ms), raw_json),
    )
    conn.commit()


def upsert_market_mids(
    conn: sqlite3.Connection,
    *,
    network: str,
    mids: dict[str, Any],
    captured_at_ms: int | None = None,
) -> int:
    captured_at_ms = captured_at_ms or utc_now_ms()
    payload = mids.get("mids") if isinstance(mids.get("mids"), dict) else mids
    rows = []
    for coin, mid in payload.items():
        raw = {"coin": coin, "mid": mid}
        rows.append(
            (
                _sha256([network, str(coin), str(captured_at_ms)]),
                network,
                str(coin),
                _float_or_none(mid),
                captured_at_ms,
                ms_to_utc_iso(captured_at_ms),
                _json(raw),
            )
        )

    conn.executemany(
        """
        INSERT OR IGNORE INTO market_mids (
            mid_key, network, coin, mid, captured_at_ms, captured_at_utc, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_market_candles(
    conn: sqlite3.Connection,
    *,
    network: str,
    candles: Iterable[dict[str, Any]],
) -> int:
    now = utc_now_ms()
    rows = []
    for candle in candles:
        coin = _str_or_none(candle.get("s"))
        interval = _str_or_none(candle.get("i"))
        open_time_ms = _int_or_none(candle.get("t"))
        if coin is None or interval is None or open_time_ms is None:
            continue
        close_time_ms = _int_or_none(candle.get("T"))
        rows.append(
            (
                _sha256([network, coin, interval, str(open_time_ms)]),
                network,
                coin,
                interval,
                open_time_ms,
                close_time_ms,
                ms_to_utc_iso(open_time_ms),
                ms_to_utc_iso(close_time_ms),
                _float_or_none(candle.get("o")),
                _float_or_none(candle.get("h")),
                _float_or_none(candle.get("l")),
                _float_or_none(candle.get("c")),
                _float_or_none(candle.get("v")),
                _int_or_none(candle.get("n")),
                _json(candle),
                now,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO market_candles (
            candle_key, network, coin, interval, open_time_ms, close_time_ms,
            open_time_utc, close_time_utc, open, high, low, close, volume,
            trades_count, raw_json, inserted_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candle_key) DO UPDATE SET
            close_time_ms = excluded.close_time_ms,
            close_time_utc = excluded.close_time_utc,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            trades_count = excluded.trades_count,
            raw_json = excluded.raw_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_market_trades(
    conn: sqlite3.Connection,
    *,
    network: str,
    trades: Iterable[dict[str, Any]],
) -> int:
    now = utc_now_ms()
    rows = []
    for trade in trades:
        coin = _str_or_none(trade.get("coin"))
        time_ms = _int_or_none(trade.get("time"))
        if coin is None or time_ms is None:
            continue
        price = _float_or_none(trade.get("px"))
        size = _float_or_none(trade.get("sz"))
        trade_id = _str_or_none(trade.get("tid"))
        rows.append(
            (
                _sha256([network, coin, str(time_ms), trade_id or _json(trade)]),
                network,
                coin,
                time_ms,
                ms_to_utc_iso(time_ms),
                _str_or_none(trade.get("side")),
                price,
                size,
                price * size if price is not None and size is not None else None,
                _str_or_none(trade.get("hash")),
                trade_id,
                _json(trade),
                now,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO market_trades (
            trade_key, network, coin, time_ms, time_utc, side, price, size,
            notional, trade_hash, trade_id, raw_json, inserted_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_key) DO UPDATE SET
            raw_json = excluded.raw_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def insert_orderbook_snapshot(
    conn: sqlite3.Connection,
    *,
    network: str,
    coin: str,
    snapshot: dict[str, Any],
) -> int:
    time_ms = _int_or_none(snapshot.get("time")) or utc_now_ms()
    levels = snapshot.get("levels") if isinstance(snapshot.get("levels"), list) else [[], []]
    bids = levels[0] if len(levels) > 0 and isinstance(levels[0], list) else []
    asks = levels[1] if len(levels) > 1 and isinstance(levels[1], list) else []
    snapshot_coin = _str_or_none(snapshot.get("coin")) or coin
    snapshot_id = _sha256([network, snapshot_coin, str(time_ms), _json(snapshot)])
    now = utc_now_ms()

    conn.execute(
        """
        INSERT OR IGNORE INTO orderbook_snapshots (
            snapshot_id, network, coin, time_ms, time_utc, best_bid, best_ask, raw_json, inserted_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            network,
            snapshot_coin,
            time_ms,
            ms_to_utc_iso(time_ms),
            _float_or_none(bids[0].get("px")) if bids else None,
            _float_or_none(asks[0].get("px")) if asks else None,
            _json(snapshot),
            now,
        ),
    )

    conn.execute("DELETE FROM orderbook_levels WHERE snapshot_id = ?", (snapshot_id,))
    rows = []
    for side, side_levels in (("bid", bids), ("ask", asks)):
        for index, level in enumerate(side_levels):
            rows.append(
                (
                    snapshot_id,
                    side,
                    index,
                    _float_or_none(level.get("px")),
                    _float_or_none(level.get("sz")),
                    _int_or_none(level.get("n")),
                )
            )

    conn.executemany(
        """
        INSERT INTO orderbook_levels (
            snapshot_id, side, level_index, price, size, orders_count
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_asset_contexts(
    conn: sqlite3.Connection,
    *,
    network: str,
    contexts: Iterable[dict[str, Any]],
    captured_at_ms: int | None = None,
) -> int:
    captured_at_ms = captured_at_ms or utc_now_ms()
    rows = []
    for item in contexts:
        coin = _str_or_none(item.get("coin"))
        kind = _str_or_none(item.get("kind")) or "unknown"
        ctx = item.get("ctx") if isinstance(item.get("ctx"), dict) else item
        if coin is None:
            continue
        rows.append(
            (
                _sha256([network, kind, coin, str(captured_at_ms)]),
                network,
                kind,
                coin,
                captured_at_ms,
                ms_to_utc_iso(captured_at_ms),
                _float_or_none(ctx.get("markPx")),
                _float_or_none(ctx.get("midPx")),
                _float_or_none(ctx.get("oraclePx")),
                _float_or_none(ctx.get("prevDayPx")),
                _float_or_none(ctx.get("funding")),
                _float_or_none(ctx.get("openInterest")),
                _float_or_none(ctx.get("dayNtlVlm")),
                _float_or_none(ctx.get("circulatingSupply")),
                _json(item),
            )
        )

    conn.executemany(
        """
        INSERT OR IGNORE INTO asset_contexts (
            context_key, network, kind, coin, captured_at_ms, captured_at_utc,
            mark_price, mid_price, oracle_price, previous_day_price, funding,
            open_interest, day_notional_volume, circulating_supply, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def _sha256(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


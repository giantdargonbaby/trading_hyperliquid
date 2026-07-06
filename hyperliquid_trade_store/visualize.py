from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from hyperliquid_trade_store.storage import connect
from hyperliquid_trade_store.time_utils import parse_time_ms


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    start_time_ms = parse_time_ms(args.start)
    end_time_ms = parse_time_ms(args.end)
    coin = args.coin.strip()
    if not coin:
        parser.error("--coin cannot be empty")

    conn = connect(args.db)
    try:
        candles = load_candles(
            conn,
            network=args.network,
            coin=coin,
            interval=args.interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_candles=args.max_candles,
        )
    finally:
        conn.close()

    output = args.output or default_output_path(args.network, coin, args.interval)
    write_player_html(
        output,
        network=args.network,
        coin=coin,
        interval=args.interval,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        candles=candles,
        initial_window=args.initial_window,
    )

    print(f"wrote {output}")
    print(f"candles: {len(candles)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperliquid-kline-player",
        description="Generate a standalone candlestick playback HTML from stored Hyperliquid candles.",
    )
    parser.add_argument("--db", default="data/hyperliquid.sqlite", type=Path, help="SQLite database path.")
    parser.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    parser.add_argument("--coin", required=True, help='Market symbol, for example "BTC" or "ETH".')
    parser.add_argument("--interval", default="1m", help='Candle interval, for example "1m", "5m", or "1h".')
    parser.add_argument("--start", help="Start time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--end", help="End time. Accepts epoch ms, epoch seconds, or ISO-8601.")
    parser.add_argument("--max-candles", type=int, default=5000, help="Maximum candles embedded into the HTML.")
    parser.add_argument("--initial-window", type=int, default=120, help="Initial number of candles visible in the chart.")
    parser.add_argument("--output", type=Path, help="Output HTML path. Defaults to reports/<network>_<coin>_<interval>_kline_player.html.")
    return parser


def load_candles(
    conn: sqlite3.Connection,
    *,
    network: str,
    coin: str,
    interval: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    max_candles: int,
) -> list[dict[str, Any]]:
    filters = [
        "network = ?",
        "coin = ?",
        "interval = ?",
        "open IS NOT NULL",
        "high IS NOT NULL",
        "low IS NOT NULL",
        "close IS NOT NULL",
    ]
    params: list[Any] = [network, coin, interval]
    if start_time_ms is not None:
        filters.append("open_time_ms >= ?")
        params.append(start_time_ms)
    if end_time_ms is not None:
        filters.append("open_time_ms <= ?")
        params.append(end_time_ms)

    params.append(max(1, max_candles))
    rows = conn.execute(
        f"""
        SELECT
            open_time_ms,
            close_time_ms,
            open_time_utc,
            close_time_utc,
            open,
            high,
            low,
            close,
            volume,
            trades_count
        FROM market_candles
        WHERE {" AND ".join(filters)}
        ORDER BY open_time_ms ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "t": int(row["open_time_ms"]),
            "T": int(row["close_time_ms"]) if row["close_time_ms"] is not None else None,
            "time": row["open_time_utc"],
            "closeTime": row["close_time_utc"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]) if row["volume"] is not None else 0.0,
            "trades": int(row["trades_count"]) if row["trades_count"] is not None else 0,
        }
        for row in rows
    ]


def write_player_html(
    output: Path,
    *,
    network: str,
    coin: str,
    interval: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    candles: list[dict[str, Any]],
    initial_window: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    title = f"{coin} {interval} K-line Playback"
    payload = {
        "title": title,
        "network": network,
        "coin": coin,
        "interval": interval,
        "startTimeMs": start_time_ms,
        "endTimeMs": end_time_ms,
        "initialWindow": max(20, initial_window),
        "candles": candles,
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    output.write_text(
        HTML_TEMPLATE.replace("__HTML_TITLE__", html.escape(title)).replace("__PAYLOAD_JSON__", data_json),
        encoding="utf-8",
    )


def default_output_path(network: str, coin: str, interval: str) -> Path:
    safe_coin = re.sub(r"[^A-Za-z0-9_.-]+", "_", coin).strip("_") or "market"
    safe_interval = re.sub(r"[^A-Za-z0-9_.-]+", "_", interval).strip("_") or "interval"
    return Path("reports") / f"{network}_{safe_coin}_{safe_interval}_kline_player.html"


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__HTML_TITLE__</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1412;
      --panel: #171d1b;
      --panel-2: #1f2724;
      --grid: rgba(180, 190, 176, 0.14);
      --text: #eef3ed;
      --muted: #a7b3aa;
      --up: #1fbf75;
      --down: #ef5b5b;
      --accent: #e3b341;
      --border: #2f3934;
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }

    .app {
      height: 100vh;
      display: grid;
      grid-template-rows: 58px minmax(0, 1fr) 72px;
      min-width: 320px;
    }

    .topbar, .controls {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 14px;
      border-color: var(--border);
      background: var(--panel);
    }

    .topbar {
      border-bottom: 1px solid var(--border);
      min-width: 0;
    }

    .brand {
      min-width: 0;
      display: flex;
      align-items: baseline;
      gap: 10px;
      flex: 1;
    }

    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      white-space: nowrap;
    }

    .meta {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .stage {
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 248px;
    }

    .chart-wrap {
      position: relative;
      min-width: 0;
      min-height: 0;
      background: #111714;
    }

    canvas {
      display: block;
      width: 100%;
      height: 100%;
    }

    .empty {
      position: absolute;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 15px;
      text-align: center;
      padding: 24px;
    }

    .side {
      border-left: 1px solid var(--border);
      background: var(--panel);
      padding: 14px;
      display: grid;
      align-content: start;
      gap: 14px;
      min-width: 0;
    }

    .metric {
      border-bottom: 1px solid var(--border);
      padding-bottom: 10px;
    }

    .metric:last-child { border-bottom: 0; }
    .label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
      text-transform: uppercase;
    }

    .value {
      font-variant-numeric: tabular-nums;
      font-size: 18px;
      line-height: 1.25;
      word-break: break-word;
    }

    .value.small { font-size: 13px; color: var(--muted); }
    .up { color: var(--up); }
    .down { color: var(--down); }

    .controls {
      border-top: 1px solid var(--border);
      display: grid;
      grid-template-columns: auto minmax(120px, 1fr) auto auto auto;
      min-width: 0;
    }

    .buttons {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    button, select {
      height: 36px;
      border: 1px solid var(--border);
      background: var(--panel-2);
      color: var(--text);
      border-radius: 6px;
      font: inherit;
    }

    button {
      width: 38px;
      display: inline-grid;
      place-items: center;
      cursor: pointer;
      font-size: 15px;
    }

    button.primary {
      width: 46px;
      border-color: #6f5b24;
      background: #342a13;
      color: var(--accent);
    }

    button:hover, select:hover { border-color: #536057; }
    button:disabled { opacity: 0.45; cursor: default; }

    .range-wrap {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }

    .counter {
      color: var(--muted);
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .select-group {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    select {
      min-width: 82px;
      padding: 0 8px;
    }

    @media (max-width: 820px) {
      body { overflow: auto; }
      .app {
        height: 100dvh;
        grid-template-rows: 54px minmax(360px, 1fr) 118px;
      }
      .stage {
        grid-template-columns: 1fr;
        grid-template-rows: minmax(0, 1fr) auto;
      }
      .side {
        border-left: 0;
        border-top: 1px solid var(--border);
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px 14px;
      }
      .controls {
        grid-template-columns: 1fr;
        gap: 8px;
      }
      .buttons { justify-content: center; }
      .select-group { justify-content: space-between; }
      h1 { font-size: 16px; }
      .meta { font-size: 12px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <h1 id="title"></h1>
        <div class="meta" id="rangeLabel"></div>
      </div>
    </header>

    <main class="stage">
      <section class="chart-wrap">
        <canvas id="chart"></canvas>
        <div class="empty" id="empty">No candles found for this range.</div>
      </section>
      <aside class="side">
        <div class="metric">
          <div class="label">Time</div>
          <div class="value small" id="timeValue">-</div>
        </div>
        <div class="metric">
          <div class="label">Close</div>
          <div class="value" id="closeValue">-</div>
        </div>
        <div class="metric">
          <div class="label">Change</div>
          <div class="value" id="changeValue">-</div>
        </div>
        <div class="metric">
          <div class="label">OHLC</div>
          <div class="value small" id="ohlcValue">-</div>
        </div>
        <div class="metric">
          <div class="label">Volume</div>
          <div class="value" id="volumeValue">-</div>
        </div>
        <div class="metric">
          <div class="label">Trades</div>
          <div class="value" id="tradesValue">-</div>
        </div>
      </aside>
    </main>

    <footer class="controls">
      <div class="buttons">
        <button id="resetBtn" title="Reset">|&lt;</button>
        <button id="prevBtn" title="Previous">&lt;</button>
        <button class="primary" id="playBtn" title="Play">&gt;</button>
        <button id="nextBtn" title="Next">&gt;</button>
      </div>
      <div class="range-wrap">
        <input id="timeline" type="range" min="0" value="0">
        <div class="counter" id="counter">0 / 0</div>
      </div>
      <label class="select-group">Speed
        <select id="speed">
          <option value="250">4x</option>
          <option value="500" selected>2x</option>
          <option value="1000">1x</option>
          <option value="1500">0.7x</option>
        </select>
      </label>
      <label class="select-group">Window
        <select id="windowSize">
          <option value="60">60</option>
          <option value="120" selected>120</option>
          <option value="240">240</option>
          <option value="480">480</option>
        </select>
      </label>
      <div class="counter" id="hoverValue">-</div>
    </footer>
  </div>

  <script>
    const DATA = __PAYLOAD_JSON__;
    const candles = DATA.candles;
    const canvas = document.getElementById("chart");
    const ctx = canvas.getContext("2d");
    const empty = document.getElementById("empty");
    const title = document.getElementById("title");
    const rangeLabel = document.getElementById("rangeLabel");
    const playBtn = document.getElementById("playBtn");
    const prevBtn = document.getElementById("prevBtn");
    const nextBtn = document.getElementById("nextBtn");
    const resetBtn = document.getElementById("resetBtn");
    const timeline = document.getElementById("timeline");
    const speed = document.getElementById("speed");
    const windowSize = document.getElementById("windowSize");
    const counter = document.getElementById("counter");
    const hoverValue = document.getElementById("hoverValue");
    const timeValue = document.getElementById("timeValue");
    const closeValue = document.getElementById("closeValue");
    const changeValue = document.getElementById("changeValue");
    const ohlcValue = document.getElementById("ohlcValue");
    const volumeValue = document.getElementById("volumeValue");
    const tradesValue = document.getElementById("tradesValue");

    let current = 0;
    let playing = false;
    let timer = null;
    let hover = null;

    title.textContent = `${DATA.coin} ${DATA.interval}`;
    rangeLabel.textContent = `${DATA.network} - ${formatRange()}`;
    timeline.max = Math.max(0, candles.length - 1);
    current = Math.max(0, candles.length - 1);
    timeline.value = String(current);
    windowSize.value = String(pickWindowSize(DATA.initialWindow));

    function pickWindowSize(value) {
      const allowed = [60, 120, 240, 480];
      return allowed.reduce((best, item) => Math.abs(item - value) < Math.abs(best - value) ? item : best, allowed[0]);
    }

    function formatRange() {
      if (!candles.length) return "empty";
      return `${shortTime(candles[0].t)} - ${shortTime(candles[candles.length - 1].t)}`;
    }

    function shortTime(ms) {
      return new Date(ms).toLocaleString(undefined, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      });
    }

    function fullTime(ms) {
      return new Date(ms).toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      });
    }

    function fmt(n, digits = 2) {
      if (!Number.isFinite(n)) return "-";
      const abs = Math.abs(n);
      const fraction = abs >= 1000 ? 2 : abs >= 1 ? digits : 6;
      return n.toLocaleString(undefined, { maximumFractionDigits: fraction });
    }

    function resize() {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    }

    function visibleRange() {
      const count = Number(windowSize.value);
      const end = Math.min(current, candles.length - 1);
      const start = Math.max(0, end - count + 1);
      return { start, end, rows: candles.slice(start, end + 1) };
    }

    function draw() {
      const rect = canvas.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;
      ctx.clearRect(0, 0, width, height);
      empty.style.display = candles.length ? "none" : "flex";
      updateStats();
      updateButtons();
      if (!candles.length) return;

      const pad = { left: 58, right: 78, top: 24, bottom: 34 };
      const volumeHeight = Math.max(76, height * 0.22);
      const chartBottom = height - pad.bottom - volumeHeight - 12;
      const volumeTop = chartBottom + 18;
      const chartHeight = Math.max(120, chartBottom - pad.top);
      const plotWidth = Math.max(120, width - pad.left - pad.right);
      const { start, rows } = visibleRange();
      const highs = rows.map(c => c.high);
      const lows = rows.map(c => c.low);
      const maxHigh = Math.max(...highs);
      const minLow = Math.min(...lows);
      const padding = Math.max((maxHigh - minLow) * 0.08, maxHigh * 0.0005);
      const topPrice = maxHigh + padding;
      const bottomPrice = minLow - padding;
      const priceRange = topPrice - bottomPrice || 1;
      const maxVolume = Math.max(1, ...rows.map(c => c.volume));
      const slot = plotWidth / Math.max(1, rows.length);
      const candleWidth = Math.max(2, Math.min(12, slot * 0.58));

      function xAt(i) { return pad.left + (i + 0.5) * slot; }
      function yAt(price) { return pad.top + (topPrice - price) / priceRange * chartHeight; }
      function volumeY(volume) { return height - pad.bottom - (volume / maxVolume) * volumeHeight; }

      drawGrid(width, height, pad, chartHeight, chartBottom, volumeTop, topPrice, bottomPrice, yAt);

      rows.forEach((candle, localIndex) => {
        const x = xAt(localIndex);
        const up = candle.close >= candle.open;
        const color = up ? "#1fbf75" : "#ef5b5b";
        const wickTop = yAt(candle.high);
        const wickBottom = yAt(candle.low);
        const bodyTop = yAt(Math.max(candle.open, candle.close));
        const bodyBottom = yAt(Math.min(candle.open, candle.close));
        const bodyHeight = Math.max(1, bodyBottom - bodyTop);

        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, wickTop);
        ctx.lineTo(x, wickBottom);
        ctx.stroke();

        ctx.fillStyle = color;
        ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);

        ctx.globalAlpha = 0.36;
        ctx.fillRect(x - candleWidth / 2, volumeY(candle.volume), candleWidth, height - pad.bottom - volumeY(candle.volume));
        ctx.globalAlpha = 1;
      });

      drawTimeLabels(rows, start, pad, plotWidth, height, slot, xAt);
      drawCurrentMarker(rows, start, slot, pad, chartBottom, height);
      drawHover(rows, start, slot, pad, yAt, xAt, chartBottom);
    }

    function drawGrid(width, height, pad, chartHeight, chartBottom, volumeTop, topPrice, bottomPrice, yAt) {
      ctx.strokeStyle = "rgba(180, 190, 176, 0.14)";
      ctx.fillStyle = "#a7b3aa";
      ctx.font = "12px ui-sans-serif, system-ui";
      ctx.textBaseline = "middle";

      for (let i = 0; i <= 5; i++) {
        const price = bottomPrice + (topPrice - bottomPrice) * i / 5;
        const y = yAt(price);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.fillText(fmt(price), width - pad.right + 10, y);
      }

      ctx.beginPath();
      ctx.moveTo(pad.left, chartBottom + 8);
      ctx.lineTo(width - pad.right, chartBottom + 8);
      ctx.moveTo(pad.left, volumeTop);
      ctx.lineTo(width - pad.right, volumeTop);
      ctx.stroke();
    }

    function drawTimeLabels(rows, start, pad, plotWidth, height, slot, xAt) {
      ctx.fillStyle = "#a7b3aa";
      ctx.font = "12px ui-sans-serif, system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      const step = Math.max(1, Math.floor(rows.length / 5));
      for (let i = 0; i < rows.length; i += step) {
        ctx.fillText(shortTime(rows[i].t), xAt(i), height - 12);
      }
      ctx.textAlign = "left";
    }

    function drawCurrentMarker(rows, start, slot, pad, chartBottom, height) {
      const localIndex = current - start;
      if (localIndex < 0 || localIndex >= rows.length) return;
      const x = pad.left + (localIndex + 0.5) * slot;
      ctx.strokeStyle = "rgba(227, 179, 65, 0.68)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, height - pad.bottom);
      ctx.stroke();
    }

    function drawHover(rows, start, slot, pad, yAt, xAt, chartBottom) {
      if (!hover) {
        hoverValue.textContent = "-";
        return;
      }
      const localIndex = Math.max(0, Math.min(rows.length - 1, Math.floor((hover.x - pad.left) / slot)));
      const candle = rows[localIndex];
      if (!candle) return;
      const x = xAt(localIndex);
      ctx.strokeStyle = "rgba(238, 243, 237, 0.28)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, canvas.getBoundingClientRect().height - pad.bottom);
      ctx.moveTo(pad.left, hover.y);
      ctx.lineTo(canvas.getBoundingClientRect().width - pad.right, hover.y);
      ctx.stroke();
      hoverValue.textContent = `${shortTime(candle.t)} - O ${fmt(candle.open)} H ${fmt(candle.high)} L ${fmt(candle.low)} C ${fmt(candle.close)}`;
    }

    function updateStats() {
      counter.textContent = candles.length ? `${current + 1} / ${candles.length}` : "0 / 0";
      timeline.value = String(current);
      const candle = candles[current];
      if (!candle) {
        timeValue.textContent = "-";
        closeValue.textContent = "-";
        changeValue.textContent = "-";
        ohlcValue.textContent = "-";
        volumeValue.textContent = "-";
        tradesValue.textContent = "-";
        return;
      }

      const change = candle.close - candle.open;
      const changePct = candle.open ? change / candle.open * 100 : 0;
      timeValue.textContent = fullTime(candle.t);
      closeValue.textContent = fmt(candle.close);
      closeValue.className = `value ${change >= 0 ? "up" : "down"}`;
      changeValue.textContent = `${change >= 0 ? "+" : ""}${fmt(change)} (${changePct >= 0 ? "+" : ""}${fmt(changePct, 2)}%)`;
      changeValue.className = `value ${change >= 0 ? "up" : "down"}`;
      ohlcValue.textContent = `O ${fmt(candle.open)} - H ${fmt(candle.high)} - L ${fmt(candle.low)} - C ${fmt(candle.close)}`;
      volumeValue.textContent = fmt(candle.volume);
      tradesValue.textContent = candle.trades.toLocaleString();
    }

    function updateButtons() {
      const disabled = !candles.length;
      playBtn.disabled = disabled;
      prevBtn.disabled = disabled || current <= 0;
      resetBtn.disabled = disabled || current <= 0;
      nextBtn.disabled = disabled || current >= candles.length - 1;
      playBtn.textContent = playing ? "||" : ">";
      playBtn.title = playing ? "Pause" : "Play";
    }

    function stop() {
      playing = false;
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      updateButtons();
    }

    function play() {
      if (!candles.length) return;
      if (current >= candles.length - 1) current = 0;
      playing = true;
      timer = setInterval(() => {
        if (current >= candles.length - 1) {
          stop();
          draw();
          return;
        }
        current += 1;
        draw();
      }, Number(speed.value));
      updateButtons();
    }

    playBtn.addEventListener("click", () => {
      if (playing) stop(); else play();
    });
    prevBtn.addEventListener("click", () => {
      stop();
      current = Math.max(0, current - 1);
      draw();
    });
    nextBtn.addEventListener("click", () => {
      stop();
      current = Math.min(candles.length - 1, current + 1);
      draw();
    });
    resetBtn.addEventListener("click", () => {
      stop();
      current = 0;
      draw();
    });
    timeline.addEventListener("input", () => {
      stop();
      current = Number(timeline.value);
      draw();
    });
    speed.addEventListener("change", () => {
      if (playing) {
        stop();
        play();
      }
    });
    windowSize.addEventListener("change", draw);

    canvas.addEventListener("mousemove", event => {
      const rect = canvas.getBoundingClientRect();
      hover = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      draw();
    });
    canvas.addEventListener("mouseleave", () => {
      hover = null;
      draw();
    });
    window.addEventListener("resize", resize);

    if (!candles.length) {
      current = 0;
      timeline.max = 0;
    }
    resize();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())

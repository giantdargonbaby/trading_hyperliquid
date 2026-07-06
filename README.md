# Hyperliquid Public Market Store

这个程序通过 Hyperliquid 官方 Python SDK 拉取公开市场数据，并存入本地 SQLite。

默认会保存：

- `market_candles`：指定交易对的 K 线
- `orderbook_snapshots` / `orderbook_levels`：指定交易对的 L2 盘口快照
- `market_mids`：全市场 mid price 快照
- `asset_contexts`：perp / spot 的公开市场上下文，例如 mark price、funding、open interest、24h volume
- `public_snapshots`：公开接口原始快照备份
- `market_trades`：可选的 websocket 实时公开成交
- `sync_runs` / `sync_state`：同步运行记录和增量游标

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使用

拉取 BTC 最近 24 小时的 1m K 线、盘口快照、全市场 mids 和资产上下文：

```bash
python -m hyperliquid_trade_store \
  --coin BTC \
  --interval 1m \
  --db data/hyperliquid.sqlite
```

指定时间范围：

```bash
python -m hyperliquid_trade_store \
  --coin ETH \
  --interval 5m \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-06T00:00:00Z
```

订阅 60 秒实时公开成交并落库：

```bash
python -m hyperliquid_trade_store \
  --coin BTC \
  --stream-trades \
  --duration-seconds 60
```

只拉 K 线，不拉盘口和上下文：

```bash
python -m hyperliquid_trade_store \
  --coin BTC \
  --skip-book \
  --skip-contexts
```

使用 testnet：

```bash
python -m hyperliquid_trade_store \
  --network testnet \
  --coin ETH
```

再次运行时，如果没有传 `--start`，程序默认会从上次保存的 candle 游标继续同步。想忽略游标，传 `--no-incremental`。

## CLI 参数说明

安装为 Python package 后可直接使用下面的命令；未安装时也可以继续用对应的 `python -m ...` 形式：

- `hyperliquid-trade-store`：等价于 `python -m hyperliquid_trade_store`
- `hyperliquid-kline-player`：等价于 `python -m hyperliquid_trade_store.visualize`
- `hyperliquid-backtest`：等价于 `python -m hyperliquid_trade_store.backtest`
- `hyperliquid-sweep`：等价于 `python -m hyperliquid_trade_store.sweep`

时间参数 `--start` / `--end` 支持 epoch 毫秒、epoch 秒或 ISO-8601，例如 `2026-07-01T00:00:00Z`。

### `hyperliquid-trade-store`

拉取 Hyperliquid 公开市场数据并写入 SQLite。

| 参数 | 必填/默认值 | 说明 |
| --- | --- | --- |
| `--coin` | 必填 | 交易对，例如 `BTC`、`ETH`，spot 可使用 `PURR/USDC` 这类符号。 |
| `--db` | 默认 `data/hyperliquid.sqlite` | SQLite 数据库路径。 |
| `--network` | 默认 `mainnet` | 网络，可选 `mainnet` / `testnet`。 |
| `--interval` | 默认 `1m` | K 线周期，可选 `1m`、`3m`、`5m`、`15m`、`30m`、`1h`、`2h`、`4h`、`8h`、`12h`、`1d`、`3d`、`1w`、`1M`。 |
| `--start` | 默认按增量游标或 `--lookback-hours` 推导 | 同步开始时间。显式传入后会覆盖增量游标。 |
| `--end` | 默认当前时间 | 同步结束时间。 |
| `--lookback-hours` | 默认 `24` | 未传 `--start` 且没有可用增量游标时，向前回看多少小时拉取 K 线。 |
| `--timeout` | 默认 `20` | HTTP 请求超时时间，单位秒。 |
| `--no-incremental` | 默认启用增量 | 忽略已保存的 candle 游标，按 `--start` 或 `--lookback-hours` 重新拉取。 |
| `--skip-candles` | 默认不跳过 | 不拉取 K 线。 |
| `--skip-book` | 默认不跳过 | 不拉取 L2 盘口快照。 |
| `--skip-contexts` | 默认不跳过 | 不拉取全市场 mids 和资产上下文快照。 |
| `--stream-trades` | 默认关闭 | 通过 websocket 订阅实时公开成交并写入 `market_trades`。 |
| `--duration-seconds` | 默认 `60` | 开启 `--stream-trades` 后的订阅时长，单位秒。 |

### `hyperliquid-kline-player`

从本地 `market_candles` 生成离线 K 线播放 HTML。

| 参数 | 必填/默认值 | 说明 |
| --- | --- | --- |
| `--coin` | 必填 | 交易对，例如 `BTC`、`ETH`。 |
| `--db` | 默认 `data/hyperliquid.sqlite` | SQLite 数据库路径。 |
| `--network` | 默认 `mainnet` | 网络，可选 `mainnet` / `testnet`。 |
| `--interval` | 默认 `1m` | K 线周期，例如 `1m`、`5m`、`1h`。 |
| `--start` | 可选 | 只导出不早于该时间的 K 线。 |
| `--end` | 可选 | 只导出不晚于该时间的 K 线。 |
| `--max-candles` | 默认 `5000` | 最多嵌入 HTML 的 K 线数量。 |
| `--initial-window` | 默认 `120` | 页面初始可视 K 线根数。 |
| `--output` | 默认 `reports/<network>_<coin>_<interval>_kline_player.html` | 输出 HTML 路径。 |

### `hyperliquid-backtest`

使用本地 K 线运行单次策略回测，必要时可自动补齐缺失 K 线。

| 参数 | 必填/默认值 | 说明 |
| --- | --- | --- |
| `--coin` | 至少提供 `--coin` 或 `--coins` | 指定一个交易对；可重复传入，也支持逗号分隔。 |
| `--coins` | 至少提供 `--coin` 或 `--coins` | 逗号分隔的多交易对，例如 `BTC,ETH,SOL`。 |
| `--db` | 默认 `data/hyperliquid.sqlite` | SQLite 数据库路径。 |
| `--network` | 默认 `mainnet` | 网络，可选 `mainnet` / `testnet`。 |
| `--interval` | 默认 `1m` | K 线周期，例如 `1m`、`5m`、`1h`。 |
| `--start` | 可选 | 回测开始时间。 |
| `--end` | 可选 | 回测结束时间。 |
| `--lookback-hours` | 默认 `168` | 自动补数据且未传 `--start` 时，向前回看多少小时。 |
| `--no-auto-fetch` | 默认允许自动补数据 | 本地 K 线不足时不联网补数据，直接失败。 |
| `--fetch-timeout` | 默认 `20` | 自动补数据的 HTTP 请求超时时间，单位秒。 |
| `--max-candles` | 默认 `20000` | 每个交易对最多加载的 K 线数量。 |
| `--strategy` | 默认 `sma-cross` | 内置策略名，或自定义策略路径 `path.py:ClassName`。 |
| `--param` | 默认无 | 策略参数，格式 `key=value`，可重复传入；值会自动解析为 bool、int、float 或字符串。 |
| `--initial-cash` | 默认 `10000` | 初始资金。 |
| `--fee-bps` | 默认 `4` | 每次交易手续费，单位 bps。 |
| `--slippage-bps` | 默认 `0` | 每次交易滑点，单位 bps。 |
| `--min-notional` | 默认 `1` | 小于该名义金额的交易会被忽略。 |
| `--allow-short` | 默认关闭 | 允许策略输出负目标权重，即允许做空。 |
| `--max-gross-exposure` | 默认 `1` | 最大总敞口，按 `sum(abs(weights))` 限制。 |
| `--max-position-weight` | 默认 `1` | 单个交易对最大绝对目标权重。 |
| `--output-dir` | 默认自动生成 | 输出目录，包含 `summary.json`、`equity_curve.csv`、`trades.csv`。 |
| `--write-baseline` | 可选 | 将本次稳定指标写入指定 baseline JSON。 |
| `--baseline` | 可选 | 与指定 baseline JSON 比较本次结果。 |
| `--tolerance-pct` | 默认 `0.01` | baseline 比较允许的指标漂移，单位百分点。 |

### `hyperliquid-sweep`

对某个回测策略做参数网格搜索。

| 参数 | 必填/默认值 | 说明 |
| --- | --- | --- |
| `--coin` | 至少提供 `--coin` 或 `--coins` | 指定一个交易对；可重复传入，也支持逗号分隔。 |
| `--coins` | 至少提供 `--coin` 或 `--coins` | 逗号分隔的多交易对，例如 `BTC,ETH,SOL`。 |
| `--db` | 默认 `data/hyperliquid.sqlite` | SQLite 数据库路径。 |
| `--network` | 默认 `mainnet` | 网络，可选 `mainnet` / `testnet`。 |
| `--interval` | 默认 `1m` | K 线周期，例如 `1m`、`5m`、`1h`。 |
| `--start` | 可选 | 扫参回测开始时间。 |
| `--end` | 可选 | 扫参回测结束时间。 |
| `--lookback-hours` | 默认 `168` | 自动补数据且未传 `--start` 时，向前回看多少小时。 |
| `--no-auto-fetch` | 默认允许自动补数据 | 本地 K 线不足时不联网补数据，直接失败。 |
| `--fetch-timeout` | 默认 `20` | 自动补数据的 HTTP 请求超时时间，单位秒。 |
| `--max-candles` | 默认 `20000` | 每个交易对最多加载的 K 线数量。 |
| `--strategy` | 默认 `strategies/whale_volume_strategy.py:WhaleVolumeStrategy` | 要扫参的策略，格式同回测命令。 |
| `--param` | 默认无 | 固定策略参数，格式 `key=value`，可重复传入。 |
| `--sweep-param` | 默认无 | 网格参数，格式 `key=value1,value2`，可重复传入，例如 `--sweep-param step_weight=0.1,0.2`。 |
| `--initial-cash` | 默认 `10000` | 初始资金。 |
| `--fee-bps` | 默认 `4` | 每次交易手续费，单位 bps。 |
| `--slippage-bps` | 默认 `0` | 每次交易滑点，单位 bps。 |
| `--min-notional` | 默认 `1` | 小于该名义金额的交易会被忽略。 |
| `--allow-short` | 默认关闭 | 允许策略输出负目标权重，即允许做空。 |
| `--max-gross-exposure` | 默认 `1` | 最大总敞口，按 `sum(abs(weights))` 限制。 |
| `--max-position-weight` | 默认 `1` | 单个交易对最大绝对目标权重。 |
| `--output-dir` | 默认 `reports/backtests/parameter_sweep` | 扫参输出目录。 |
| `--top` | 默认 `20` | 在 stdout 和 `sweep_report.md` 中展示排名前多少的组合。 |

## 查询数据

查看最近 10 根 K 线：

```bash
sqlite3 data/hyperliquid.sqlite '
select open_time_utc, coin, interval, open, high, low, close, volume, trades_count
from market_candles
order by open_time_ms desc
limit 10;
'
```

查看最近一次盘口前 5 档：

```bash
sqlite3 data/hyperliquid.sqlite '
with latest as (
  select snapshot_id
  from orderbook_snapshots
  where coin = "BTC"
  order by time_ms desc
  limit 1
)
select side, level_index, price, size, orders_count
from orderbook_levels
where snapshot_id in (select snapshot_id from latest)
order by side, level_index
limit 10;
'
```

查看实时成交：

```bash
sqlite3 data/hyperliquid.sqlite '
select time_utc, coin, side, price, size, notional, trade_id
from market_trades
order by time_ms desc
limit 10;
'
```

## K 线播放图

生成指定时间段内的离线 K 线播放页面：

```bash
python -m hyperliquid_trade_store.visualize \
  --db data/hyperliquid.sqlite \
  --coin BTC \
  --interval 1m \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-06T00:00:00Z \
  --output reports/btc_1m_player.html
```

生成后直接打开 `reports/btc_1m_player.html`。页面支持播放、暂停、逐根前进/后退、拖动时间轴、切换播放速度和可视窗口大小。

## 历史数据回测

使用已经存入 `market_candles` 的历史 K 线跑策略回测：

```bash
python -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coin BTC \
  --interval 1m \
  --strategy sma-cross \
  --param fast=20 \
  --param slow=60 \
  --initial-cash 10000 \
  --fee-bps 4 \
  --output-dir reports/backtests/btc_sma
```

回测命令默认会先检查本地 `market_candles`。如果指定币种、周期、时间段没有足够 K 线，会先通过 Hyperliquid API 自动补数据，再重新加载本地数据跑策略。没有传 `--start` 时，自动补数据默认拉最近 168 小时，可用 `--lookback-hours` 调整。

如果只想使用本地已有数据，不允许联网补数据：

```bash
python -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coin HYPE \
  --interval 1h \
  --strategy buy-and-hold \
  --no-auto-fetch
```

多币种回测：

```bash
python -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coins BTC,ETH,SOL \
  --interval 1m \
  --strategy momentum \
  --param lookback=120 \
  --param top_n=1
```

输出目录会包含：

- `summary.json`：收益、最大回撤、交易数、手续费等汇总指标
- `equity_curve.csv`：每根 K 线后的权益曲线、现金、持仓、收盘价
- `trades.csv`：每次调仓成交记录

内置策略：

- `buy-and-hold`：等权买入并持有所有币种
- `sma-cross`：均线金叉持有，死叉空仓，可用 `fast` / `slow` 参数
- `momentum`：选择过去 `lookback` 根 K 线涨幅最高的前 `top_n` 个币种

自定义策略：

```bash
python -m hyperliquid_trade_store.backtest \
  --coin BTC \
  --strategy strategies/example_strategy.py:ExampleStrategy \
  --param lookback=3
```

自定义类需要实现：

```python
def generate_targets(self, *, histories, portfolio, timestamp_ms):
    return {"BTC": 1.0}
```

`histories` 只包含当前 K 线之前的数据，订单按当前 K 线开盘价成交，权益按当前 K 线收盘价结算。返回 `{}` 表示保持现有仓位，返回 `{"BTC": 0.0}` 表示把 BTC 目标仓位调到 0。

鲸鱼放量策略示例：

```bash
python -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coin HYPE \
  --interval 1m \
  --strategy strategies/whale_volume_strategy.py:WhaleVolumeStrategy \
  --param volume_lookback=3 \
  --param spike_multiplier=3 \
  --param volume_drop_ratio=0.5 \
  --param pullback_tolerance_pct=0.5 \
  --param breakout_buffer_pct=0 \
  --param step_weight=0.2 \
  --param max_abs_weight=1 \
  --allow-short \
  --max-position-weight 1 \
  --max-gross-exposure 1
```

可调参数：

- `volume_lookback`：当前 K 线之前用于计算均量的 K 线根数
- `spike_multiplier`：当前成交量超过均量多少倍视为鲸鱼入场
- `volume_drop_ratio`：成交量跌到均量多少比例以下视为鲸鱼离场
- `pullback_tolerance_pct`：鲸鱼离场时，价格距离最高点/最低点的容忍比例
- `breakout_buffer_pct`：做市商阶段需要额外突破最高点/最低点多少比例才触发动作
- `step_weight`：每次加仓/反手的目标仓位比例，例如 `0.2` 表示 20%
- `max_abs_weight`：策略自身允许的最大绝对仓位，`1` 表示满仓

批量扫参：

```bash
python -m hyperliquid_trade_store.sweep \
  --db data/hyperliquid.sqlite \
  --coin HYPE \
  --interval 1m \
  --start 2026-07-05T06:28:00Z \
  --end 2026-07-06T06:28:00Z \
  --strategy strategies/whale_volume_strategy.py:WhaleVolumeStrategy \
  --sweep-param volume_lookback=2,3,5 \
  --sweep-param spike_multiplier=2,2.5,3 \
  --sweep-param volume_drop_ratio=0.35,0.5,0.65 \
  --sweep-param pullback_tolerance_pct=0.3,0.5,0.8 \
  --sweep-param breakout_buffer_pct=0,0.1 \
  --sweep-param step_weight=0.1,0.2,0.3 \
  --param max_abs_weight=1 \
  --allow-short \
  --max-position-weight 1 \
  --max-gross-exposure 1 \
  --no-auto-fetch \
  --output-dir reports/backtests/hype_24h_whale_sweep
```

扫参输出：

- `sweep_results.csv`：全部参数组合的收益、回撤、交易数、手续费等指标
- `sweep_report.md`：按收益排序的摘要报告
- `best_params.json`：最佳组合参数
- `best_run/`：最佳组合的 `summary.json`、`equity_curve.csv`、`trades.csv`

## MyQuant 策略适配

`third_party/strategy-master` 保存了 `myquant/strategy` 的 Apache-2.0 源码快照。原策略依赖掘金 `gmsdk` / `talib` / 股票池配置，不能直接运行在本项目的 Hyperliquid 数据上；本项目在 `strategies/myquant_classic_strategies.py` 里把其中只依赖 K 线的经典策略适配成当前回测接口：

- `MyquantMovingAverageStrategy`：MA 均线穿越
- `MyquantMacdStrategy`：MACD 趋势
- `MyquantRsiStrategy`：RSI 超买超卖
- `MyquantBollTrendStrategy`：BOLL 三轨趋势
- `MyquantTurtleStrategy`：Turtle 高低点突破

单独运行示例：

```bash
python -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coin HYPE \
  --interval 5m \
  --start 2026-06-29T06:25:00Z \
  --end 2026-07-06T06:25:00Z \
  --strategy strategies/myquant_classic_strategies.py:MyquantRsiStrategy \
  --param period=14 \
  --param oversold=30 \
  --param overbought=70 \
  --no-auto-fetch
```

一键验证当前适配策略：

```bash
python scripts/validate_myquant_strategies.py
```

输出在 `reports/backtests/myquant_hype_validation/`：

- `report.md`：HYPE `5m` 7 天和 `1m` 24 小时对比报告
- `comparison.csv`：所有策略汇总结果
- 每个策略目录：`summary.json`、`equity_curve.csv`、`trades.csv`

回归测试 baseline：

```bash
python -m hyperliquid_trade_store.backtest \
  --coin BTC \
  --strategy sma-cross \
  --param fast=20 \
  --param slow=60 \
  --write-baseline reports/backtests/btc_sma/baseline.json

python -m hyperliquid_trade_store.backtest \
  --coin BTC \
  --strategy sma-cross \
  --param fast=20 \
  --param slow=60 \
  --baseline reports/backtests/btc_sma/baseline.json
```

## 说明

- REST 部分保存的是公开行情快照和 K 线。
- 实时逐笔成交来自公开 websocket `trades` 订阅，只有运行 `--stream-trades` 时才会持续写入。
- Hyperliquid 的 candle snapshot 只返回最近一段可用数据；更长期的数据建议定时运行这个程序持续落库。

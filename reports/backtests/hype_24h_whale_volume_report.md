# HYPE 24h Whale Volume Strategy Report

## Strategy File

- Strategy: `strategies/whale_volume_strategy.py:WhaleVolumeStrategy`
- Output: `reports/backtests/hype_24h_whale_volume`
- Benchmark: `reports/backtests/hype_24h_buy_and_hold_benchmark`

## Backtest Scope

- Coin: HYPE
- Interval: 1m
- Range: 2026-07-05T06:28:00+00:00 to 2026-07-06T06:28:00+00:00
- Candles: 1441
- Initial cash: 10000.00
- Fee: 4 bps per trade
- Shorting: enabled
- Max absolute position: 100%
- Step position size: 20%
- Execution model: signal confirmed on a closed candle, rebalance at next candle open, mark equity at candle close

## Implemented Rules

1. Whale entry:
   - Use the latest closed candle as the signal candle.
   - Compare its volume against the previous 3 closed candles.
   - If `volume > previous_3_avg_volume * 3`, treat it as whale entry.
   - If `close > open`, whale direction is up and target position increases by +20%.
   - If `close < open`, whale direction is down and target position decreases by -20%.

2. Whale still present:
   - Continue tracking the high and low since whale entry.
   - If volume does not fall below `previous_3_avg_volume * 0.5`, hold current position.

3. Whale leaves and market maker phase:
   - If volume falls below `previous_3_avg_volume * 0.5`, check price tolerance.
   - Up whale: price must remain within 0.5% of the high since whale entry.
   - Down whale: price must remain within 0.5% of the low since whale entry.
   - If tolerance is respected, enter market maker phase.

4. Market maker reaction:
   - If price breaks further along whale direction, flatten and open 20% in the opposite direction.
   - If price breaks opposite whale direction, add 20% in the original whale direction.
   - Position is capped at +/-100%.

## Results

| Strategy | Final Equity | Return | Max Drawdown | Trades | Total Fees | Sharpe Per Bar |
|---|---:|---:|---:|---:|---:|---:|
| whale-volume-follow | 9683.7799 | -3.1622% | -3.2986% | 230 | 221.2219 | -0.0467 |
| buy-and-hold benchmark | 10330.2521 | 3.3025% | -2.6388% | 1 | 4.0000 | 0.0258 |

## Observations

- The whale-volume strategy underperformed buy-and-hold over this 24h window.
- The strategy traded 230 times, and fees reached 221.22, which is a large drag relative to the 10000 initial capital.
- The strategy ended with an open long position of about 1936.89 notional, roughly 20% exposure.
- The raw rules are active enough to create many reversals on 1m candles. The next improvement should likely add a cooldown, stronger breakout confirmation, or a minimum whale volume threshold in absolute terms.

## Re-run Command

```bash
python3 -m hyperliquid_trade_store.backtest \
  --db data/hyperliquid.sqlite \
  --coin HYPE \
  --interval 1m \
  --start 2026-07-05T06:28:00Z \
  --end 2026-07-06T06:28:00Z \
  --strategy strategies/whale_volume_strategy.py:WhaleVolumeStrategy \
  --initial-cash 10000 \
  --fee-bps 4 \
  --allow-short \
  --max-position-weight 1 \
  --max-gross-exposure 1 \
  --no-auto-fetch \
  --output-dir reports/backtests/hype_24h_whale_volume
```

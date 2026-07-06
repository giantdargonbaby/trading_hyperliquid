# HYPE 7-Day Strategy Backtest Report

## Scope

- Market: Hyperliquid public market data
- Coin: HYPE
- Interval: 1h
- Range: 2026-06-29T06:00:00+00:00 to 2026-07-06T06:00:00+00:00
- Candles: 169
- Initial cash: 10000.00
- Fee: 4 bps per trade
- Execution model: rebalance at candle open, mark equity at candle close

## Result Comparison

| Rank | Strategy | Parameters | Final Equity | Return | Max Drawdown | Trades | Fees | Sharpe Per Bar |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | buy-and-hold | - | 11254.9579 | 12.5496% | -7.8472% | 1 | 4.0000 | 0.0904 |
| 2 | momentum | lookback=24, top_n=1 | 10854.0326 | 8.5403% | -2.4449% | 8 | 20.5911 | 0.0993 |
| 3 | sma-cross | fast=20, slow=60 | 10299.4172 | 2.9942% | -4.8227% | 5 | 12.3303 | 0.0453 |

## Strategy Notes

### buy-and-hold

This strategy bought HYPE once and held it through the full window. It produced the highest absolute return, but also carried the largest drawdown among the three strategies.

### sma-cross

This strategy used fast=20 and slow=60. It reduced downside compared with buy-and-hold, but it also missed a large part of the upside over this 7-day sample.

### momentum

This strategy used lookback=24 and top_n=1. Since this run used a single coin, the strategy alternated between holding HYPE and cash based on 24-hour momentum. It did not beat buy-and-hold on absolute return, but it had the smallest max drawdown and the best per-bar Sharpe among the three.

## Takeaway

For this 7-day HYPE 1h sample, buy-and-hold had the best raw return. Momentum had the best drawdown profile and the best Sharpe-style score. SMA cross was the weakest in both return and Sharpe for these parameters.

This is a short sample, so the result is not enough to select a production strategy by itself. It is useful as a regression fixture and a first sanity check for strategy behavior.

## Output Files

- buy-and-hold: reports/backtests/hype_7d_buy_and_hold
- sma-cross: reports/backtests/hype_7d_sma_cross
- momentum: reports/backtests/hype_7d_momentum
- comparison CSV: reports/backtests/hype_7d_strategy_comparison.csv

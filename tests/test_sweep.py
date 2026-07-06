from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hyperliquid_trade_store.backtest import Candle
from hyperliquid_trade_store.sweep import parse_sweep_params, run_parameter_sweep, write_sweep_outputs


class SweepTest(unittest.TestCase):
    def test_parse_sweep_params_expands_grid(self) -> None:
        grid, keys = parse_sweep_params(["volume_lookback=2,3", "step_weight=0.1,0.2"])

        self.assertEqual(keys, ["volume_lookback", "step_weight"])
        self.assertEqual(
            grid,
            [
                {"volume_lookback": 2, "step_weight": 0.1},
                {"volume_lookback": 2, "step_weight": 0.2},
                {"volume_lookback": 3, "step_weight": 0.1},
                {"volume_lookback": 3, "step_weight": 0.2},
            ],
        )

    def test_run_parameter_sweep_writes_report_and_best_run(self) -> None:
        aligned = [(index, {"BTC": candle(index, 100 + index, 101 + index)}) for index in range(1, 8)]
        rows, best = run_parameter_sweep(
            aligned,
            coins=["BTC"],
            strategy_spec="sma-cross",
            fixed_params={},
            grid=[{"fast": 1, "slow": 2}, {"fast": 2, "slow": 3}],
            param_keys=["fast", "slow"],
            initial_cash=10_000,
            fee_bps=0,
            slippage_bps=0,
            min_notional=0,
            allow_short=False,
            max_gross_exposure=1,
            max_position_weight=1,
            network="mainnet",
            interval="1m",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertIn("result", best)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            write_sweep_outputs(output_dir, rows, ["fast", "slow"], best_result=best, top=2)

            self.assertTrue((output_dir / "sweep_results.csv").exists())
            self.assertTrue((output_dir / "sweep_report.md").exists())
            self.assertTrue((output_dir / "best_params.json").exists())
            self.assertTrue((output_dir / "best_run" / "summary.json").exists())


def candle(timestamp: int, open_price: float, close_price: float) -> Candle:
    return Candle(
        coin="BTC",
        time_ms=timestamp,
        time_utc=f"2026-07-06T00:00:0{timestamp}+00:00",
        open=open_price,
        high=max(open_price, close_price),
        low=min(open_price, close_price),
        close=close_price,
        volume=10,
        trades_count=1,
    )


if __name__ == "__main__":
    unittest.main()

import unittest

from offline.backtests.metrics import calculate_performance_metrics


class PerformanceMetricsTest(unittest.TestCase):
    def test_return_drawdown_exposure_and_trade_metrics(self):
        result = calculate_performance_metrics(
            [100.0, 110.0, 105.0, 120.0],
            initial_capital=100.0,
            periods_per_year=365.0,
            exposure=[False, True, True],
            trade_pnls=[10.0, -5.0, 20.0],
            turnover_notional=250.0,
        )

        self.assertAlmostEqual(result["return_pct"], 20.0)
        self.assertAlmostEqual(result["max_drawdown_abs"], 5.0)
        self.assertAlmostEqual(result["max_drawdown_pct"], 100 * 5 / 110)
        self.assertEqual(result["max_underwater_periods"], 1)
        self.assertAlmostEqual(result["exposure_pct"], 200 / 3)
        self.assertEqual(result["trade_count"], 3)
        self.assertAlmostEqual(result["win_rate_pct"], 200 / 3)
        self.assertAlmostEqual(result["profit_factor"], 6.0)
        self.assertAlmostEqual(result["expectancy"], 25 / 3)
        self.assertAlmostEqual(result["turnover_pct"], 250.0)
        self.assertIsNotNone(result["sharpe"])
        self.assertIsNotNone(result["sortino"])
        self.assertIsNotNone(result["calmar"])

    def test_constant_losses_have_negative_sortino_not_zero(self):
        result = calculate_performance_metrics(
            [100.0, 99.0, 98.0, 97.0],
            initial_capital=100.0,
            periods_per_year=252.0,
        )
        self.assertLess(result["sortino"], 0.0)
        self.assertLess(result["cvar_95_pct"], 0.0)

    def test_frequency_dependent_metrics_are_not_invented(self):
        result = calculate_performance_metrics(
            [100.0, 101.0, 102.0], initial_capital=100.0,
        )
        self.assertIsNone(result["annualized_return_pct"])
        self.assertIsNone(result["sharpe"])
        self.assertIsNone(result["sortino"])
        self.assertIsNone(result["calmar"])

    def test_rejects_curve_without_initial_capital_anchor(self):
        with self.assertRaises(ValueError):
            calculate_performance_metrics([101.0, 102.0], initial_capital=100.0)


if __name__ == "__main__":
    unittest.main()

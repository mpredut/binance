import copy
import unittest

from offline.backtests.promotion import PromotionThresholds, evaluate_promotion


def _report(mean=1.0, worst=-2.0, drawdown=4.0, returns=(1.0,) * 20):
    windows = [
        {"key": f"240m/fold-{index:02d}", "return_pct": value}
        for index, value in enumerate(returns, start=1)
    ]
    scenario = {
        "aggregate": {
            "mean_return_pct": mean,
            "worst_return_pct": worst,
            "worst_max_drawdown_pct": drawdown,
        },
        "windows": windows,
    }
    return {
        "dataset": {"sha256": "abc", "bars": 3772},
        "walk_forward": {
            "train": 720, "validation": 180, "test": 90,
            "step": 90, "warmup": 40,
        },
        "scenarios": {"central": copy.deepcopy(scenario),
                      "stress": copy.deepcopy(scenario)},
    }


class PromotionGateTest(unittest.TestCase):
    def test_candidate_must_improve_mean_without_worsening_tail_or_drawdown(self):
        baseline = _report(returns=(0.0,) * 20)
        candidate = _report(
            mean=1.2, worst=-1.9, drawdown=3.8,
            returns=(0.2,) * 12 + (-0.1,) * 8,
        )

        result = evaluate_promotion(baseline, candidate)

        self.assertTrue(result["promote"])
        self.assertTrue(result["scenarios"]["central"]["passed"])
        self.assertEqual(
            result["scenarios"]["stress"]["wins_ties_losses"],
            {"wins": 12, "ties": 0, "losses": 8},
        )

    def test_one_failed_stress_scenario_blocks_promotion(self):
        baseline = _report(returns=(0.0,) * 20)
        candidate = _report(
            mean=1.2, worst=-1.9, drawdown=3.8, returns=(0.2,) * 20,
        )
        candidate["scenarios"]["stress"]["aggregate"]["worst_max_drawdown_pct"] = 4.5

        result = evaluate_promotion(baseline, candidate)

        self.assertFalse(result["promote"])
        self.assertFalse(result["scenarios"]["stress"]["checks"]["drawdown_preserved"])

    def test_same_strategy_is_not_a_promotion(self):
        baseline = _report()
        result = evaluate_promotion(baseline, copy.deepcopy(baseline))
        self.assertFalse(result["promote"])
        self.assertFalse(
            result["scenarios"]["central"]["checks"]["material_mean_improvement"]
        )

    def test_different_dataset_is_rejected(self):
        baseline = _report()
        candidate = _report()
        candidate["dataset"]["sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "dataset"):
            evaluate_promotion(baseline, candidate, PromotionThresholds())


if __name__ == "__main__":
    unittest.main()


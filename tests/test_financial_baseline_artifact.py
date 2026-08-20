import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "offline" / "research" / "hype_dataset"
BASELINE = DATASET_DIR / "financial_baseline_v1.json"


class FinancialBaselineArtifactTest(unittest.TestCase):
    def test_versioned_baseline_has_reproducible_contract(self):
        with BASELINE.open(encoding="utf-8") as handle:
            report = json.load(handle)
        with (DATASET_DIR / "manifest.json").open(encoding="utf-8") as handle:
            manifest = json.load(handle)

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["candidate_name"], "base_v2_live")
        self.assertEqual(
            report["dataset"]["sha256"],
            manifest["datasets"]["240"]["sha256"],
        )
        self.assertEqual(report["dataset"]["bars"], 3772)
        self.assertEqual(report["walk_forward"]["parameter_selection"],
                         "none; fixed profile")
        self.assertFalse(report["walk_forward"]["shuffle"])
        self.assertFalse(report["code"]["worktree_dirty"])
        self.assertRegex(report["code"]["commit"], r"^[0-9a-f]{40}$")

        self.assertEqual(set(report["scenarios"]), {"central", "stress"})
        for name, scenario in report["scenarios"].items():
            with self.subTest(scenario=name):
                self.assertFalse(
                    scenario["assumptions"]["calibrated_from_real_fills"]
                )
                self.assertEqual(
                    scenario["assumptions"]["execution"]["intrabar_policy"],
                    "worst_case",
                )
                self.assertEqual(len(scenario["windows"]), 31)
                self.assertEqual(scenario["aggregate"]["window_count"], 31)
                self.assertEqual(scenario["aggregate"]["total_test_bars"], 2790)


if __name__ == "__main__":
    unittest.main()

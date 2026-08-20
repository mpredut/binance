import unittest

from offline.backtests.hype_candidates import (
    financial_priority_candidates,
    hype_240_candidates,
)


class HypeCandidatesTest(unittest.TestCase):
    def test_priority_set_is_unique_fixed_and_starts_with_live(self):
        candidates = financial_priority_candidates()
        names = [candidate.name for candidate in candidates]

        self.assertEqual(names[0], "live")
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            names,
            [
                "live", "tp4", "dca15", "dca_progressive025", "A_trail",
                "B_dcabrake", "overlay650t8",
            ],
        )

    def test_tp4_and_dca15_are_one_factor_candidates(self):
        by_name = {
            candidate.name: candidate
            for candidate in financial_priority_candidates()
        }

        self.assertEqual(by_name["tp4"].overrides, {"takeprofit_pct": 4.0})
        self.assertEqual(by_name["dca15"].overrides, {"dca_drop_pct": 1.5})
        self.assertEqual(
            by_name["dca_progressive025"].overrides,
            {"dca_spacing_growth_pct": 0.25},
        )

    def test_walk_forward_registry_keeps_historical_report_names(self):
        self.assertEqual(
            [candidate.name for candidate in hype_240_candidates()],
            [
                "live", "overlay_orig", "overlay650t8", "A_adaptive_trail",
                "B_dca_brake", "tp_4", "dca_drop_1_5",
            ],
        )


if __name__ == "__main__":
    unittest.main()

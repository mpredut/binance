import unittest

from offline.backtests.execution import (
    ExecutionModel,
    choose_intrabar_scenario,
    split_order_fill,
)


class ExecutionModelTest(unittest.TestCase):
    def test_spread_changes_limit_touch_and_market_slippage_is_adverse(self):
        plain = ExecutionModel()
        stressed = ExecutionModel(spread_bps=20, market_slippage_bps=30)
        self.assertTrue(plain.limit_touched("buy", high=101, low=99.95, limit=100))
        self.assertFalse(stressed.limit_touched("buy", high=101, low=99.95, limit=100))
        self.assertAlmostEqual(stressed.market_price("buy", 100), 100.4)
        self.assertAlmostEqual(stressed.market_price("sell", 100), 99.6)

    def test_partial_fill_uses_original_quantity_and_prorates_amount(self):
        order = {"side": "BUY", "kind": "DCA", "qty": 10.0, "amount": 100.0}
        first, qty1, done1 = split_order_fill(
            order, quantity_key="qty", amount_key="amount", ratio=0.4,
        )
        second, qty2, done2 = split_order_fill(
            order, quantity_key="qty", amount_key="amount", ratio=0.4,
        )
        third, qty3, done3 = split_order_fill(
            order, quantity_key="qty", amount_key="amount", ratio=0.4,
        )
        self.assertEqual((qty1, qty2, qty3), (4.0, 4.0, 2.0))
        self.assertEqual((done1, done2, done3), (False, False, True))
        self.assertAlmostEqual(first["amount"] + second["amount"] + third["amount"], 100)
        self.assertEqual(first["kind"], "DCA")
        self.assertEqual(second["kind"], "DCA_PARTIAL")

    def test_worst_case_selects_lower_return(self):
        result = choose_intrabar_scenario(
            ExecutionModel(intrabar_policy="worst_case"),
            lambda model: {
                "return_pct": 2.0 if model.intrabar_policy == "sell_first" else -1.0,
                "max_drawdown_pct": 3.0, "fills": 1, "ambiguous_bars": 1,
            },
        )
        self.assertEqual(result["intrabar_policy_selected"], "buy_first")
        self.assertEqual(result["return_pct"], -1.0)
        self.assertEqual(set(result["intrabar_scenarios"]), {"buy_first", "sell_first"})

    def test_invalid_assumptions_are_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionModel(partial_fill_ratio=0)
        with self.assertRaises(ValueError):
            ExecutionModel(spread_bps=-1)


if __name__ == "__main__":
    unittest.main()

import unittest

from providers.base import MarketDataProvider
from providers.quantity import (
    balance_cap_quantity,
    decide_quantity,
    fee_cap_quantity,
    resolve_assets,
)


class Provider(MarketDataProvider):
    name = "fake"
    def __init__(self, balances, policy=999):
        self.balances = balances
        self.policy = policy
    def get_current_price(self, symbol): return 100.0
    def supports_symbol(self, symbol): return True
    def free_balance(self, asset): return self.balances.get(asset, 0.0)
    def policy_cap_quantity(self, symbol, side, price, qty, available_qty, **kwargs):
        return min(qty, self.policy)


class QuantityDecisionTest(unittest.TestCase):
    def test_assets_are_resolved_once_for_long_suffixes(self):
        self.assertEqual(resolve_assets("TAOUSDC"), ("TAO", "USDC"))
        self.assertEqual(resolve_assets("BTCUSDC"), ("BTC", "USDC"))

    def test_resolver_covers_non_usdc_inventory_quotes_without_usdt(self):
        self.assertEqual(resolve_assets("TLVRON"), ("TLV", "RON"))
        self.assertEqual(resolve_assets("HYPEUSD"), ("HYPE", "USD"))
        self.assertEqual(resolve_assets("TAOUSDT"), ("TAOUSDT", None))

    def test_buy_converts_quote_balance_to_base_quantity(self):
        cap, asset = balance_cap_quantity(
            lambda a: {"USDC": 450}[a], "TAOUSDC", "BUY", 225)
        self.assertEqual((cap, asset), (2.0, "USDC"))

    def test_sell_uses_base_balance(self):
        decision = decide_quantity(
            Provider({"TAO": 1.0}, policy=0.6),
            "TAOUSDC", "SELL", 225, 2.0)
        self.assertEqual(decision.requested_qty, 2.0)
        self.assertEqual(decision.balance_cap, 1.0)
        self.assertEqual(decision.policy_cap, 0.6)
        self.assertEqual(decision.final_qty, 0.6)

    def test_none_is_error_but_zero_is_real_insufficient_balance(self):
        unavailable = decide_quantity(
            Provider({"USDC": None}), "TAOUSDC", "BUY", 225, 1)
        empty = decide_quantity(
            Provider({"USDC": 0}), "TAOUSDC", "BUY", 225, 1)
        self.assertEqual(unavailable.refuse_reason, "balance_unavailable")
        self.assertEqual(empty.refuse_reason, "insufficient_funds")

    def test_fee_cap_operates_on_base_quantity(self):
        self.assertAlmostEqual(fee_cap_quantity(2.0, 0.01), 2.0 / 1.01)


if __name__ == "__main__":
    unittest.main()

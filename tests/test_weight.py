import unittest

import priceAnalysis as pa


class TestWeightFunction(unittest.TestCase):
    def test_get_weight_for_cash_permission_at_quant_time_requires_order_type(self):
        weight = pa.get_weight_for_cash_permission_at_quant_time("BTCUSDC", order_type="BUY")

        self.assertTrue(weight is None or isinstance(weight, float))


if __name__ == "__main__":
    unittest.main()

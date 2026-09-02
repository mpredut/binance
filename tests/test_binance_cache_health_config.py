"""Fail-fast validation for Binance account-cache safety configuration."""

import math
import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import binance_cache_health as health


class BinanceCacheHealthConfigTest(unittest.TestCase):
    def test_positive_finite_values_are_accepted(self):
        self.assertEqual(health._positive_finite(1, "TEST_VALUE"), 1.0)
        self.assertEqual(health._positive_finite("0.5", "TEST_VALUE"), 0.5)

    def test_zero_negative_nan_and_infinity_are_rejected(self):
        for value in (0, -1, math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                        ValueError, "must be finite and positive"):
                    health._positive_finite(value, "TEST_VALUE")


if __name__ == "__main__":
    unittest.main()

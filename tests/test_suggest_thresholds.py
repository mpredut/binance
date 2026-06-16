#!/usr/bin/env python3
"""Teste pt modelul de sugerare praguri (partea PURA, fara retea)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from suggest_thresholds import FLOOR, thresholds_from_prices  # noqa: E402


class TestThresholds(unittest.TestCase):
    def test_flat_da_floor(self):
        up, down, upm, downm = thresholds_from_prices([100] * 40)
        self.assertEqual(up, FLOOR)
        self.assertEqual(down, FLOOR)
        self.assertEqual(upm, 0.0)

    def test_volatil_da_praguri_mari(self):
        # serie cu swing-uri ~10-30% -> praguri peste floor
        prices = [100, 90, 115, 88, 130, 85, 120] * 6
        up, down, _, _ = thresholds_from_prices(prices, window=4)
        self.assertGreater(up, FLOOR)
        self.assertGreater(down, FLOOR)

    def test_mai_volatil_prag_mai_mare(self):
        calm = [100 + (i % 2) for i in range(60)]            # ±1
        wild = [100 + (i % 2) * 30 for i in range(60)]       # ±30%
        up_calm = thresholds_from_prices(calm, window=6)[0]
        up_wild = thresholds_from_prices(wild, window=6)[0]
        self.assertGreater(up_wild, up_calm)

    def test_date_insuficiente(self):
        self.assertIsNone(thresholds_from_prices([100, 101], window=24))


if __name__ == "__main__":
    unittest.main(verbosity=2)

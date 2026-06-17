#!/usr/bin/env python3
"""
Teste pt trend_stats (Mann-Kendall + Hurst) si integrarea filtrului MK in detector.

  /home/mariusp/binance/.venv/bin/python test_trend_stats.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trend.trend_stats import mann_kendall, hurst_rs, hurst_regime  # noqa: E402
from priceAnalysis import detect_long_term_trend  # noqa: E402


class TestMannKendall(unittest.TestCase):
    def test_trend_crescator_semnificativ(self):
        s, z, p = mann_kendall([100 + 0.5 * i for i in range(24)])
        self.assertGreater(z, 0)
        self.assertLess(p, 0.01)

    def test_trend_descrescator_semnificativ(self):
        s, z, p = mann_kendall([100 - 0.5 * i for i in range(24)])
        self.assertLess(z, 0)
        self.assertLess(p, 0.01)

    def test_zgomot_alb_nesemnificativ(self):
        rng = np.random.default_rng(42)
        s, z, p = mann_kendall(100 + rng.normal(0, 1, 24))
        self.assertGreater(p, 0.1, "zgomotul pur nu trebuie sa para trend")

    def test_serie_scurta_nesemnificativa(self):
        _, _, p = mann_kendall([1, 2, 3])
        self.assertEqual(p, 1.0)


class TestHurst(unittest.TestCase):
    @staticmethod
    def _series(phi: float, n: int = 2000, seed: int = 7):
        """preturi cu log-randamente AR(1): phi>0 persistent, phi<0 anti-persistent"""
        rng = np.random.default_rng(seed)
        r = np.zeros(n)
        for i in range(1, n):
            r[i] = phi * r[i - 1] + rng.normal(0, 0.01)
        return 100 * np.exp(np.cumsum(r))

    def test_persistenta_da_h_mare(self):
        h = hurst_rs(self._series(0.6))
        self.assertGreater(h, 0.55)

    def test_mean_reverting_da_h_mic(self):
        h = hurst_rs(self._series(-0.6))
        self.assertLess(h, 0.45)

    def test_serie_scurta_da_none(self):
        self.assertIsNone(hurst_rs([100, 101, 102]))

    def test_regimuri(self):
        self.assertEqual(hurst_regime(0.65), "persistent")
        self.assertEqual(hurst_regime(0.30), "mean-reverting")
        self.assertEqual(hurst_regime(0.50), "random-walk")
        self.assertEqual(hurst_regime(None), "necunoscut")


class TestFiltruMKInDetector(unittest.TestCase):
    H = 3600.0

    def _mk_series(self, vals):
        return np.array([i * self.H for i in range(len(vals))]), np.array(vals, float)

    def test_trend_curat_trece_de_filtru(self):
        ts, px = self._mk_series([100 - 0.3 * i for i in range(96)])
        r = detect_long_term_trend(ts, px, 24, 8, 3, 2, 5, mk_alpha=0.05)
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "down")

    def test_zgomotul_e_filtrat(self):
        rng = np.random.default_rng(3)
        base = [100 - 0.3 * i for i in range(72)]            # istoric cu trend
        noise = list(base[-1] + rng.normal(0, 0.8, 24))      # fereastra curenta = zgomot pur
        ts, px = self._mk_series(base + noise)
        r = detect_long_term_trend(ts, px, 24, 8, 3, 2, 5, mk_alpha=0.05)
        self.assertIsNone(r, "fereastra curenta fara trend semnificativ -> None")

    def test_fara_filtru_comportament_vechi(self):
        ts, px = self._mk_series([100 - 0.3 * i for i in range(96)])
        r = detect_long_term_trend(ts, px, 24, 8, 3, 2, 5, mk_alpha=None)
        self.assertIsNotNone(r)


if __name__ == "__main__":
    unittest.main(verbosity=2)

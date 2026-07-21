"""
Teste pentru shadow_signals.py — în special comportamentul la GOL de date
(gap) în KalmanTrend.update(), reparat 21 iul: un gol lung (retea jos, proces
oprit) nu mai propagă viteza veche (plafonată la DT_MAX), ci resetează
filtrul (ca la warm-up) — trend-ul iese FLAT după gol, nu "încrezător" pe o
direcție stale.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_signals as ss


class TestKalmanTrendContinuous(unittest.TestCase):
    """Comportament de bază (neschimbat) pe o serie fără goluri: trend UP clar."""

    def test_detects_clear_uptrend(self):
        kf = ss.KalmanTrend()
        ts = 1_000_000.0
        price = 100.0
        out = None
        for _ in range(30):
            ts += 1.0
            price += 0.05          # ~0.05%/s * 60 = 3%/min, sigur peste prag
            out = kf.update(ts, price, epsilon=0.01)
        self.assertEqual(out["trend"], 1)

    def test_flat_series_stays_flat(self):
        kf = ss.KalmanTrend()
        ts = 1_000_000.0
        out = None
        for _ in range(30):
            ts += 1.0
            out = kf.update(ts, 100.0, epsilon=0.01)
        self.assertEqual(out["trend"], 0)


class TestKalmanTrendGapReset(unittest.TestCase):
    """21 iul: gol > GAP_RESET_SEC (300s) => reset, nu doar dt plafonat."""

    def _warm_up_uptrend(self, kf, ts, price, steps=30):
        for _ in range(steps):
            ts += 1.0
            price += 0.05
            out = kf.update(ts, price, epsilon=0.01)
        return ts, price, out

    def test_long_gap_resets_velocity_to_zero(self):
        kf = ss.KalmanTrend()
        ts, price, out = self._warm_up_uptrend(kf, 1_000_000.0, 100.0)
        self.assertEqual(out["trend"], 1, "precondiție: trend UP stabilit înainte de gol")

        # gol de 1h (> GAP_RESET_SEC=300s), pretul revine neschimbat fata de ultimul cunoscut
        ts_after_gap = ts + 3600.0
        out_after = kf.update(ts_after_gap, price, epsilon=0.01)

        self.assertEqual(out_after["vel"], 0.0, "viteza veche NU trebuie propagata peste un gol lung")
        self.assertEqual(out_after["trend"], 0, "dupa reset, trend-ul iese FLAT, nu UP stale")

    def test_short_gap_under_threshold_is_not_reset(self):
        """Un gol de 30s (sub GAP_RESET_SEC) trebuie tratat normal (dt real), nu ca reset."""
        kf = ss.KalmanTrend()
        ts, price, out = self._warm_up_uptrend(kf, 1_000_000.0, 100.0)
        self.assertEqual(out["trend"], 1)

        ts_after_gap = ts + 30.0
        price += 0.05 * 30       # trendul UP continua peste gol
        out_after = kf.update(ts_after_gap, price, epsilon=0.01)

        self.assertNotEqual(out_after["vel"], 0.0, "un gol scurt nu trebuie sa reseteze viteza la 0")
        self.assertEqual(out_after["trend"], 1, "trendul UP trebuie sa supravietuiasca unui gol scurt")

    def test_gap_reset_does_not_crash_on_first_ever_update(self):
        """Prima observatie (self.x is None) trebuie sa ramana neafectata de noua ramura de gap."""
        kf = ss.KalmanTrend()
        out = kf.update(1_000_000.0, 100.0, epsilon=0.01)
        self.assertEqual(out["trend"], 0)
        self.assertEqual(out["vel"], 0.0)

    def test_gap_boundary_exactly_at_threshold_not_reset(self):
        """La exact GAP_RESET_SEC, ramura de reset (strict >) nu trebuie sa declanseze."""
        kf = ss.KalmanTrend()
        ts, price, _ = self._warm_up_uptrend(kf, 1_000_000.0, 100.0)
        out_after = kf.update(ts + ss.GAP_RESET_SEC, price + 0.05, epsilon=0.01)
        # dt real = GAP_RESET_SEC exact => nu e strict mai mare => cale normala (nu reset la 0)
        self.assertNotEqual(out_after["vel"], 0.0)


class TestVolAndThresholds(unittest.TestCase):
    """Sanity minim pe restul modulului — neschimbate de fix-ul de gap."""

    def test_vol_1h_pct_warmup_returns_none(self):
        self.assertIsNone(ss.vol_1h_pct([100.0] * 5, sample_rate_sec=1.0))

    def test_adaptive_thresholds_none_in_warmup(self):
        re, dca = ss.adaptive_thresholds(None)
        self.assertIsNone(re)
        self.assertIsNone(dca)


if __name__ == "__main__":
    unittest.main()

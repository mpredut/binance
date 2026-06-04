import os, sys, time, unittest

os.environ.setdefault("MPLBACKEND", "Agg")   # fără backend GUI la import matplotlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import priceAnalysis as pa


def _uniform_series(days=20, step_sec=300.0, slope_per_day=1.0, base=100.0, end=None):
    end = end if end is not None else time.time()
    ts = np.arange(end - days * 86400, end, step_sec)
    pr = base + (ts - ts[0]) / 86400.0 * slope_per_day
    return ts, pr


class TestTimeBasedTrend(unittest.TestCase):
    def test_uniform_uptrend(self):
        ts, pr = _uniform_series(days=20, slope_per_day=1.0)
        r = pa.detect_long_term_trend(ts, pr, window_hours=16, step_hours=8)
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "up")

    def test_uniform_downtrend(self):
        ts, pr = _uniform_series(days=20, slope_per_day=-1.0)
        r = pa.detect_long_term_trend(ts, pr, window_hours=16, step_hours=8)
        self.assertEqual(r["direction"], "down")

    def test_window_is_time_based_not_point_count(self):
        # densitate DIFERITĂ (1min vs 10min) trebuie să dea aceeași direcție:
        # dovada că fereastra e în timp, nu în număr de puncte.
        ts_a, pr_a = _uniform_series(days=15, step_sec=60.0, slope_per_day=2.0)
        ts_b, pr_b = _uniform_series(days=15, step_sec=600.0, slope_per_day=2.0)
        ra = pa.detect_long_term_trend(ts_a, pr_a, window_hours=12, step_hours=6)
        rb = pa.detect_long_term_trend(ts_b, pr_b, window_hours=12, step_hours=6)
        self.assertEqual(ra["direction"], rb["direction"])
        # durata în zile e comparabilă deși densitatea diferă de 10x
        self.assertAlmostEqual(ra["duration_seconds"] / 86400,
                               rb["duration_seconds"] / 86400, delta=1.0)

    def test_gap_stops_trend(self):
        # trend UP cu un gol de 10 zile la mijloc → trendul se oprește la gap,
        # nu pretinde continuitate peste gol.
        ts, pr = _uniform_series(days=20, slope_per_day=1.0)
        keep = ~((ts > ts[0] + 5 * 86400) & (ts < ts[0] + 15 * 86400))
        r = pa.detect_long_term_trend(ts[keep], pr[keep], window_hours=16,
                                      step_hours=8, noise_tolerance=2)
        self.assertIsNotNone(r)
        # start după gap (cu toleranța de padding (noise+1)*window)
        self.assertGreaterEqual(r["start_timestamp"],
                                ts[0] + 15 * 86400 - (2 + 1) * 16 * 3600 - 1)

    def test_insufficient_recent_data_returns_none(self):
        # doar 2 puncte recente → fereastra recentă sub min_points → None
        ts = np.array([time.time() - 100, time.time() - 50])
        pr = np.array([100.0, 101.0])
        self.assertIsNone(pa.detect_long_term_trend(ts, pr, window_hours=16))

    def test_blocks_are_index_pairs(self):
        ts, pr = _uniform_series(days=20, slope_per_day=1.0)
        r = pa.detect_long_term_trend(ts, pr, window_hours=16, step_hours=8)
        for lo, hi in r["blocks"]:
            self.assertTrue(0 <= lo < hi <= len(ts))


if __name__ == "__main__":
    unittest.main()

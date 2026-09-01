"""test_backtest_annotations.py — it validates the "# BACKTEST: ..." annotations in
the config files (UNIFIED_BACKTEST_PLAN.md §5: "a simple test that checks
that every key in an annotation really exists in the config plus the grid is well formed").

It guards the SILENT errors that would make the pilot (scheduled_pilot.py) run on
a broken grid without anyone noticing:
  - an annotated key that no longer exists / a typo (scan returns nothing -> nothing
    but we check that every key returned has a readable LIVE value);
  - a grid with non-numeric values or fewer than 2 values (nothing to sweep);
  - DRIFT: today's LIVE value has left the tested interval [min, max] of the
    grid — a sign that the grid has fallen behind (we do NOT require EXACT membership: the pilot
    applies the midpoint, e.g. TAO mt.lost=5.25 is not a grid point, but it is inside the interval).

Today only instruments.conf has annotations; the test is structured to cover automatically
any INI file added to _INI_CONFIGS in the future.
"""
import configparser
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from offline.research.backtest_ranges import scan_backtest_ranges  # noqa: E402

# INI files ([NAME] sections) with annotations — the keys scanned come back as "SECTION.key".
_INI_CONFIGS = [os.path.join(ROOT, "instruments.conf")]


class TestBacktestAnnotations(unittest.TestCase):
    def _current_ini_value(self, path, full_key):
        section, key = full_key.split(".", 1)
        cp = configparser.ConfigParser()
        cp.read(path)
        return float(cp[section][key])

    def test_annotations_well_formed_and_live_in_range(self):
        seen_any = False
        for path in _INI_CONFIGS:
            ranges = scan_backtest_ranges(path)
            for full_key, grid in ranges.items():
                seen_any = True
                with self.subTest(config=os.path.basename(path), key=full_key):
                    # a numeric grid, >= 2 values
                    self.assertGreaterEqual(len(grid), 2,
                                            f"{full_key}: the grid has < 2 values: {grid}")
                    try:
                        vals = [float(v) for v in grid]
                    except ValueError as e:
                        self.fail(f"{full_key}: the grid has non-numeric values ({grid}): {e}")

                    # the key exists LIVE and is readable (a typo or a deleted key)
                    current = self._current_ini_value(path, full_key)

                    # deriva: valoarea live e in [min, max] al grilei testate
                    lo, hi = min(vals), max(vals)
                    self.assertTrue(lo <= current <= hi,
                                    f"{full_key}: the LIVE value {current} has left the "
                                    f"intervalul grilei [{lo}, {hi}] {grid} — grila stale?")
        self.assertTrue(seen_any, "no # BACKTEST annotation found — is the scan or the config broken?")


if __name__ == "__main__":
    unittest.main()

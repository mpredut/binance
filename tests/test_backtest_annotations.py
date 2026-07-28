"""test_backtest_annotations.py — valideaza adnotarile "# BACKTEST: ..." din
fisierele de config (UNIFIED_BACKTEST_PLAN.md §5: "un test simplu care verifica
ca fiecare cheie din adnotare chiar exista in config + grila e bine formata").

Pazeste erorile TACUTE care ar face pilotul (scheduled_pilot.py) sa ruleze pe
o grila stricata fara sa se observe:
  - cheie adnotata care nu mai exista / typo (scan nu o intoarce -> nimic de testat,
    dar verificam ca fiecare cheie intoarsa are o valoare LIVE citibila);
  - grila cu valori ne-numerice sau cu < 2 valori (nu are ce sweepui);
  - DERIVA: valoarea LIVE de azi a iesit din intervalul testat [min, max] al
    grilei — semnal ca grila a ramas in urma (NU cerem apartenenta EXACTA: pilotul
    aplica media, ex. TAO mt.lost=5.25 nu e punct de grila, dar e in interval).

Azi doar instruments.conf are adnotari; testul e structurat sa acopere automat
orice fisier INI adaugat in _INI_CONFIGS pe viitor.
"""
import configparser
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))

from backtest_ranges import scan_backtest_ranges  # noqa: E402

# Fisiere INI (sectiuni [NUME]) cu adnotari — cheile scan-uite vin ca "SECTION.key".
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
                    # grila numerica, >= 2 valori
                    self.assertGreaterEqual(len(grid), 2,
                                            f"{full_key}: grila are < 2 valori: {grid}")
                    try:
                        vals = [float(v) for v in grid]
                    except ValueError as e:
                        self.fail(f"{full_key}: grila are valori ne-numerice ({grid}): {e}")

                    # cheia exista LIVE si e citibila (typo / cheie stearsa)
                    current = self._current_ini_value(path, full_key)

                    # deriva: valoarea live e in [min, max] al grilei testate
                    lo, hi = min(vals), max(vals)
                    self.assertTrue(lo <= current <= hi,
                                    f"{full_key}: valoarea LIVE {current} a iesit din "
                                    f"intervalul grilei [{lo}, {hi}] {grid} — grila stale?")
        self.assertTrue(seen_any, "nicio adnotare # BACKTEST gasita — scan/config stricat?")


if __name__ == "__main__":
    unittest.main()

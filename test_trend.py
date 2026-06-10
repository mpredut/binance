#!/usr/bin/env python3
"""
Teste pt detect_long_term_trend (priceAnalysis) — cazurile care au produs
raportari gresite (ex. TAO: 4 zile scadere raportate ca trend UP).

  /home/mariusp/binance/.venv/bin/python test_trend.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from priceAnalysis import detect_long_term_trend  # noqa: E402

H = 3600.0


def series(*segments):
    """segments = liste de preturi orare; intoarce (timestamps, prices)."""
    px = [p for seg in segments for p in seg]
    ts = [i * H for i in range(len(px))]
    return np.array(ts), np.array(px, float)


def run(ts, px, **kw):
    base = dict(window_hours=24, step_hours=8, min_consecutive_blocks=3,
                noise_tolerance=2, min_points_per_window=5)
    base.update(kw)
    return detect_long_term_trend(ts, px, **base)


def down(h, start=100.0, rate=0.3):
    return [start - rate * i for i in range(h)]


def up(h, start=100.0, rate=0.3):
    return [start + rate * i for i in range(h)]


class TestCazulTAO(unittest.TestCase):
    """Cazul raportat: panta descrescatoare de 4 zile dar 'current trend' UP."""

    def test_bounce_de_o_zi_contra_scaderii_nu_e_trend_up(self):
        d = down(96)                                  # 4 zile scadere
        b = up(24, start=d[-1], rate=0.25)            # 24h bounce
        r = run(*series(d, b))
        self.assertIsNone(r, "bounce-ul de 24h NU e un trend UP coerent — trebuie None")

    def test_bounce_sustinut_2_zile_devine_trend_up_cu_durata_corecta(self):
        d = down(96)
        b = up(48, start=d[-1], rate=0.25)            # bounce sustinut 2 zile
        r = run(*series(d, b))
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "up")
        dur_days = r["duration_seconds"] / 86400
        self.assertLess(dur_days, 2.6, "durata trebuie ~2 zile (bounce-ul), nu 4-6")


class TestDurata(unittest.TestCase):
    def test_durata_nu_depaseste_span_ul_datelor(self):
        ts, px = series(down(96))                     # 4 zile date
        r = run(ts, px)
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "down")
        self.assertLessEqual(r["duration_seconds"], ts[-1] - ts[0] + 1,
                             "durata raportata era > decat TOATE datele (bug-ul vechi: +72h inventate)")

    def test_scadere_pura_da_durata_aproape_de_span(self):
        ts, px = series(down(96))
        r = run(ts, px)
        self.assertGreater(r["duration_seconds"] / 86400, 3.0, "trendul real e ~4 zile")


class TestZgomot(unittest.TestCase):
    def test_zgomot_in_toleranta_nu_rupe_trendul(self):
        # scadere 2 zile + fereastra de zgomot (urcare 16h) + scadere alte 2 zile
        a = down(48)
        z = up(16, start=a[-1], rate=0.1)
        b = down(48, start=z[-1])
        r = run(*series(a, z, b))
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "down")
        self.assertGreater(r["duration_seconds"] / 86400, 3.5,
                           "zgomotul tolerat nu trebuie sa taie durata trendului real")

    def test_trend_up_curat(self):
        r = run(*series(up(96)))
        self.assertIsNotNone(r)
        self.assertEqual(r["direction"], "up")


class TestGapuri(unittest.TestCase):
    def test_gap_in_date_opreste_confirmarea(self):
        # 2 zile down, GAP de 2 zile (fara puncte), apoi 2 zile down recente
        old = down(48, start=130)
        recent = down(48, start=115)
        ts_old = [i * H for i in range(48)]
        ts_recent = [(i + 96) * H for i in range(48)]     # gap intre 48h si 96h
        ts = np.array(ts_old + ts_recent)
        px = np.array(old + recent, float)
        r = run(ts, px)
        if r is not None:
            self.assertLessEqual(r["duration_seconds"] / 3600, 50,
                                 "trendul nu are voie sa se intinda peste gap")

    def test_date_insuficiente_da_none(self):
        ts, px = series(down(10))
        self.assertIsNone(run(ts, px))


if __name__ == "__main__":
    unittest.main(verbosity=2)

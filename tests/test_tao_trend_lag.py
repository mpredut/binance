#!/usr/bin/env python3
"""
test_tao_trend_lag.py — reproduce intrebarea: dupa ce TAO a inceput sa SCADA de la
varf, de ce a ramas trendul "up" (si a blocat TP-ul)?

Detectorul (detect_long_term_trend) ia directia din panta ULTIMELOR window_hours (24h).
Dupa varf, fereastra de 24h inca contine urcusul -> panta pozitiva -> "up", chiar daca
pretul scade DEJA. Abia cand fereastra se umple cu scaderea, directia devine "down".
Testul masoara acest LAG: cate ore dupa varf ramane "up" cat pretul scade.

Ruleaza pe server (numpy):  ~/binance/myenv/bin/python test_tao_trend_lag.py
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from priceAnalysis import detect_long_term_trend  # noqa: E402

H = 3600.0


def build_series(climb_days=5, climb_from=233.0, peak=287.0, decline_to=265.0, decline_hours=36):
    """Serie orara: urcus lin climb_days zile pana la peak, apoi scadere decline_hours ore."""
    ts, px, t = [], [], 0.0
    n_up = climb_days * 24
    for h in range(n_up):
        ts.append(t); px.append(climb_from + (peak - climb_from) * h / (n_up - 1)); t += H
    for h in range(1, decline_hours + 1):
        ts.append(t); px.append(peak - (peak - decline_to) * h / decline_hours); t += H
    return np.array(ts), np.array(px)


def direction_at(ts, px, i, **kw):
    tr = detect_long_term_trend(ts[:i + 1], px[:i + 1], window_hours=24, step_hours=8,
                                detection_lag_hours=48.0, **kw)
    return (tr or {}).get("direction")


class TestTrendLagPeDeclin(unittest.TestCase):
    def setUp(self):
        self.ts, self.px = build_series()
        self.peak_i = int(np.argmax(self.px))

    def test_arata_lagul(self):
        print(f"\nPEAK la +{self.peak_i}h, pret {self.px[self.peak_i]:.1f}")
        print("ore_dupa_varf | pret  | trend_raportat")
        flip_h = None
        for i in range(self.peak_i, len(self.ts)):
            d = direction_at(self.ts, self.px, i)
            after = i - self.peak_i
            if after % 4 == 0 or (d == "down" and flip_h is None):
                print(f"   +{after:>2}h       | {self.px[i]:.1f} | {d}")
            if d == "down" and flip_h is None:
                flip_h = after
        print(f"\n=> Trendul a ramas 'up' inca ~{flip_h}h DUPA varf, cat pretul scadea "
              f"de la {self.px[self.peak_i]:.1f} la {self.px[self.peak_i + (flip_h or 0)]:.1f}.")
        print("   In tot acest interval, TP-ul a fost blocat de 'not is_trend_up'.")

    def test_chiar_la_varf_e_up(self):
        # fix la varf, inca urca pe 24h -> 'up' (corect, dar aici incepe problema)
        self.assertEqual(direction_at(self.ts, self.px, self.peak_i), "up")

    def test_dupa_destul_declin_devine_down(self):
        # la finalul scaderii (fereastra de 24h plina de scadere) trebuie sa fie 'down'
        self.assertEqual(direction_at(self.ts, self.px, len(self.ts) - 1), "down")

    def test_exista_lag_pe_declin(self):
        # CHEIA: imediat dupa varf (cativa h de scadere reala) inca raporteaza 'up'
        i = self.peak_i + 6  # 6h dupa varf, pretul a scazut deja
        self.assertLess(self.px[i], self.px[self.peak_i], "pretul chiar a scazut")
        self.assertEqual(direction_at(self.ts, self.px, i), "up",
                         "bug demonstrat: scade dar inca raporteaza 'up'")


if __name__ == "__main__":
    unittest.main(verbosity=2)

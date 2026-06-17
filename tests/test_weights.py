#!/usr/bin/env python3
"""
Teste pt get_trade_weight (ponderile gaussiene de cash-permission) — bug-urile
reparate: scara Zona 1 vs Zona 2/3, inversarea contra-trend la capatul batran,
cusatura la exact T, Zona 3 care ignora alinierea.

  /home/mariusp/binance/.venv/bin/python test_weights.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from priceAnalysis import get_trade_weight  # noqa: E402

T = 14


def w(trend_len, trend="up", order_type="BUY", **kw):
    _, ws = get_trade_weight(T=T, trend_len=trend_len, trend=trend,
                             order_type=order_type, **kw)
    return float(ws[0])


class TestAliniat(unittest.TestCase):
    """BUY+up / SELL+down: gaussiana scalata la varf (mijloc ~0.95)."""

    def test_mijlocul_da_ponderea_maxima(self):
        self.assertAlmostEqual(w(7), 0.95, delta=0.02,
                               msg="varful curbei trebuie ~0.95, nu ~0.11 (bug-ul de scara)")

    def test_capatul_tanar_da_pondere_mica(self):
        self.assertLess(w(0.5), 0.25, "trend tanar = posibil zgomot -> prudent")

    def test_dupa_varf_plafon_la_mijloc_ipoteza_lindy_validata(self):
        # validat empiric (trend_survival.py): trendul batran continua la fel de
        # probabil ca la mijloc -> ne purtam ca la mijloc, nu coboram pe gaussiana
        self.assertAlmostEqual(w(10), 0.95, delta=0.02)
        self.assertAlmostEqual(w(13), 0.95, delta=0.02)

    def test_creste_spre_mijloc(self):
        self.assertLess(w(0.5), w(3))
        self.assertLess(w(3), w(7))

    def test_scara_coerenta_cu_zona_2(self):
        # inainte de fix: 13.9 zile -> 0.021 si 14.1 zile -> 0.86 (salt de 40x)
        self.assertGreater(w(13.5), 0.1)
        self.assertLess(w(14.1) / max(w(13.5), 1e-9), 7,
                        "saltul Zona1->Zona2 trebuie rezonabil, nu 40x")

    def test_sell_pe_down_e_aliniat(self):
        self.assertAlmostEqual(w(7, trend="down", order_type="SELL"), 0.95, delta=0.02)


class TestContraTrend(unittest.TestCase):
    """SELL+up / BUY+down: mijloc -> ~0.02 (nu tranzactiona), capete -> ~0.13-0.15."""

    def test_mijlocul_blocheaza(self):
        self.assertAlmostEqual(w(7, order_type="SELL"), 0.02, delta=0.01)

    def test_capatul_tanar_permite_putin_cel_batran_ramane_blocat(self):
        # cu plafonul Lindy validat: trendul batran se poarta ca la mijloc,
        # deci contra-trade-ul ramane blocat si la batranete
        self.assertGreater(w(0.5, order_type="SELL"), 0.1)
        self.assertAlmostEqual(w(13, order_type="SELL"), 0.02, delta=0.01)

    def test_fara_plateau_curba_e_simetrica(self):
        # comportamentul clasic (gaussiana pura) ramane disponibil
        young = w(0.5, order_type="SELL", lindy_plateau=False)
        old = w(13, order_type="SELL", lindy_plateau=False)
        self.assertAlmostEqual(young, old, delta=0.03, msg="fara plafon, curba globala e simetrica")
        self.assertGreater(old, 0.1)
        self.assertLess(w(13, lindy_plateau=False), 0.25, "aliniat, fara plafon, coboara la capat")

    def test_niciodata_peste_plafonul_contra_trend(self):
        for tl in (0.5, 3, 7, 10, 13, 15, 21):
            self.assertLessEqual(w(tl, order_type="SELL"), 0.15 + 1e-9)


class TestZone(unittest.TestCase):
    def test_cusatura_la_exact_T_nu_da_fallback(self):
        self.assertGreater(w(14.0), 0.1, "la exact T slice-ul nu mai e gol (fallback 0.05)")

    def test_zona_2_aliniat(self):
        self.assertAlmostEqual(w(15), 0.86)

    def test_zona_2_contra(self):
        self.assertAlmostEqual(w(15, order_type="SELL"), 0.15)

    def test_zona_3_respecta_alinierea(self):
        self.assertAlmostEqual(w(21), 0.22)
        self.assertAlmostEqual(w(21, order_type="SELL"), 0.15,
                               msg="contra-trend pe trend batran nu depaseste max_against_trend")


class TestEstimareT(unittest.TestCase):
    """hybrid_T: empiric favorizat cand avem date, prior cand nu (fara retea)."""

    def test_multe_episoade_domina_empiricul(self):
        from trend_survival import hybrid_T
        durs = [72.0] * 100 + [160.0] * 20            # mediana 3z, P90 ~6.7z
        r = hybrid_T(durs, prior_T=14.0)
        self.assertLessEqual(r["T"], 9, "cu n=120 episoade, T trebuie aproape de empiric (~7), nu de 14")
        self.assertGreaterEqual(r["w"], 0.75)

    def test_putine_episoade_raman_la_prior(self):
        from trend_survival import hybrid_T
        r = hybrid_T([72.0] * 5, prior_T=14.0)
        self.assertGreaterEqual(r["T"], 11, "cu 5 episoade, prior-ul trebuie sa domine")

    def test_fara_episoade_prior_curat(self):
        from trend_survival import hybrid_T
        r = hybrid_T([], prior_T=14.0)
        self.assertEqual(r["T"], 14)
        self.assertEqual(r["n"], 0)

    def test_limitele_de_siguranta(self):
        from trend_survival import hybrid_T
        self.assertGreaterEqual(hybrid_T([10.0] * 500)["T"], 4, "T nu coboara sub 4 zile")
        self.assertLessEqual(hybrid_T([2000.0] * 500)["T"], 30, "T nu urca peste 30 zile")


if __name__ == "__main__":
    unittest.main(verbosity=2)

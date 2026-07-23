"""
Teste pentru pragul de reintrare ADAPTIV din kraken/strategy.py (23 iul).

Context: investigat in research/kraken_adaptive_thresholds/ — pragul adaptiv
(K_REENTRY * vol_1h) bate pragul fix pe date reale (HYPEUSD, ~30 zile: TOTAL
+3.26% vs +2.20%). Promovat la decizie reala prin StratParams.reentry_adaptive
(implicit False — activat explicit via STRAT_REENTRY_ADAPTIVE=true), cu
fail-safe pe pragul fix daca volatilitatea nu poate fi calculata (warm-up).

Acoperire:
  - _effective_reentry_drop_pct(): fix cand reentry_adaptive=False (mereu,
    indiferent de istoricul de pret); fallback pe fix cand adaptiv=True dar
    warm-up (<20 puncte); adaptiv cand exista destul istoric.
  - Blocul de reintrare din step(): foloseste pragul EFECTIV (nu mereu cel fix).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken"))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import strategy as strat


def _make_strategy(tmp_pair="TESTPAIR_REENTRY", **param_overrides):
    """Strategy cu client mockuit (pair_info->None, foloseste precizia implicita)
    si StratParams minimal — fara fisier de stare real (pair de test, nesalvat)."""
    client = MagicMock()
    client.pair_info.return_value = None
    defaults = dict(
        currency="USD", entry_amount=100.0, entry_discount_pct=0.2, dca_amount=50.0,
        dca_drop_pct=2.0, check_minutes=2.0, takeprofit_pct=1.9, max_budget=1000.0,
        max_dca_buys=10, enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=0.0,
        adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=2.2, reentry_tolerance_pct=0.05,
        reentry_adaptive=False, tp_tranches=[],
    )
    defaults.update(param_overrides)
    params = strat.StratParams(**defaults)
    return strat.Strategy(client, tmp_pair, params, dry_run=True)


class TestEffectiveReentryDropPct(unittest.TestCase):

    def test_fixed_when_adaptive_disabled(self):
        s = _make_strategy(reentry_adaptive=False, reentry_drop_pct=2.2)
        pct, source = s._effective_reentry_drop_pct()
        self.assertEqual(pct, 2.2)
        self.assertEqual(source, "fix")

    def test_fixed_stays_fixed_even_with_price_history(self):
        """Cand reentry_adaptive=False, istoricul de pret NU trebuie sa conteze deloc."""
        s = _make_strategy(reentry_adaptive=False, reentry_drop_pct=2.2)
        for i in range(30):
            s._shadow_prices.append((i * 120.0, 100.0 + (i % 3)))
        pct, source = s._effective_reentry_drop_pct()
        self.assertEqual(pct, 2.2)
        self.assertEqual(source, "fix")

    def test_adaptive_falls_back_to_fixed_during_warmup(self):
        s = _make_strategy(reentry_adaptive=True, reentry_drop_pct=2.2)
        # sub 20 de puncte -> _shadow_vol_1h() intoarce None -> fallback
        for i in range(10):
            s._shadow_prices.append((i * 120.0, 100.0 + i * 0.1))
        pct, source = s._effective_reentry_drop_pct()
        self.assertEqual(pct, 2.2)
        self.assertIn("fallback", source)
        self.assertIn("warm-up", source)

    def test_adaptive_uses_volatility_when_enough_history(self):
        s = _make_strategy(reentry_adaptive=True, reentry_drop_pct=2.2)
        # 30 de puncte, la 120s distanta, cu o mica variatie ciclica -> volatilitate nenula
        import random
        random.seed(42)
        price = 100.0
        for i in range(30):
            price *= (1 + random.uniform(-0.01, 0.01))
            s._shadow_prices.append((i * 120.0, price))
        pct, source = s._effective_reentry_drop_pct()
        self.assertIn("adaptiv", source)
        self.assertNotEqual(pct, 2.2, "pragul adaptiv nu trebuie sa coincida intamplator cu fixul")
        self.assertGreater(pct, 0)

    def test_adaptive_respects_shadow_k_reentry_env_override(self):
        s = _make_strategy(reentry_adaptive=True, reentry_drop_pct=2.2)
        import random
        random.seed(7)
        price = 100.0
        for i in range(30):
            price *= (1 + random.uniform(-0.01, 0.01))
            s._shadow_prices.append((i * 120.0, price))
        pct_k2, _ = s._effective_reentry_drop_pct()
        os.environ["SHADOW_K_REENTRY"] = "4.0"
        try:
            pct_k4, _ = s._effective_reentry_drop_pct()
        finally:
            del os.environ["SHADOW_K_REENTRY"]
        self.assertAlmostEqual(pct_k4, pct_k2 * 2.0, places=6,
                                msg="K=4.0 trebuie sa dea exact dublu fata de K=2.0 (default), acelasi vol_1h")


class TestReentryGateUsesEffectivePct(unittest.TestCase):
    """step() foloseste pragul EFECTIV (fix sau adaptiv), nu mereu cel fix direct."""

    def test_step_blocks_reentry_using_fixed_when_adaptive_disabled(self):
        s = _make_strategy(reentry_adaptive=False, reentry_drop_pct=2.2, reentry_tolerance_pct=0.0)
        s.s["last_sell_price"] = 100.0
        s.s["qty"] = 0.0
        # pret 98.5 > prag fix (100*0.978=97.8) -> ar trebui blocat
        s.step(98.5)
        self.assertFalse(s._has_open("buy"), "reintrarea trebuia blocata (pret peste pragul fix)")

    def test_step_allows_reentry_when_price_below_fixed_threshold(self):
        s = _make_strategy(reentry_adaptive=False, reentry_drop_pct=2.2, reentry_tolerance_pct=0.0)
        s.s["last_sell_price"] = 100.0
        s.s["qty"] = 0.0
        # pret 97.0 < prag fix (97.8) -> reintrarea trebuie permisa
        s.step(97.0)
        self.assertTrue(s._has_open("buy"), "reintrarea trebuia permisa (pret sub pragul fix)")


if __name__ == "__main__":
    unittest.main()

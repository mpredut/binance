"""
Teste pentru pragul de reintrare ADAPTIV din motorul spot DCA (23 iul).

Context: investigat in offline/research/kraken_adaptive_thresholds/ — pragul adaptiv
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
from unittest.mock import MagicMock, patch

KRAKEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken")
ROOT = os.path.dirname(KRAKEN_DIR)
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from strategies import spot_dca as strat  # noqa: E402


def _make_strategy(tmp_pair="TESTPAIR_REENTRY", **param_overrides):
    """Strategy cu client mockuit (pair_precision->None, foloseste precizia implicita)
    si StratParams minimal — fara fisier de stare real (pair de test, nesalvat)."""
    client = MagicMock()
    client.pair_precision.return_value = None
    defaults = dict(
        currency="USD", entry_amount=100.0, entry_discount_pct=0.2, dca_amount=50.0,
        dca_drop_pct=2.0, check_minutes=2.0, takeprofit_pct=1.9, max_budget=1000.0,
        max_dca_buys=10, enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=0.0,
        adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=2.2, reentry_tolerance_pct=0.05,
        reentry_adaptive=False, reentry_sl_bounce_pct=1.5, tp_tranches=[],
    )
    defaults.update(param_overrides)
    params = strat.StratParams(**defaults)
    return strat.Strategy(
        client, tmp_pair, params, dry_run=True,
        initial_state=strat._new_state(),
    )


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


class TestStopAwareReentry(unittest.TestCase):
    """4 aug: dupa un STOP-LOSS, reintrarea e pe REVENIRE (bounce de la minim), nu pe o
    scadere si mai jos — altfel botul ramane blocat afara cand pretul isi revine."""

    def test_stop_reentry_not_stranded_on_recovery(self):
        # BUG-ul reparat: vandut 51.19 la stop-loss, pretul revine la 55.6 (peste vanzare).
        # Regula veche (reintra doar sub 50.06) ar bloca la nesfarsit. Cea noua reintra.
        s = _make_strategy(reentry_sl_bounce_pct=1.5, reentry_drop_pct=2.2, reentry_tolerance_pct=0.0)
        s.s["qty"] = 0.0
        s.s["last_sell_price"] = 51.19
        s.s["last_exit_kind"] = "STOP"
        s.s["sl_low"] = 51.19
        s.step(55.6)
        self.assertTrue(s._has_open("buy"), "dupa STOP, revenirea trebuie sa declanseze reintrarea")

    def test_stop_reentry_blocked_until_bounce_then_enters(self):
        s = _make_strategy(reentry_sl_bounce_pct=1.5, reentry_tolerance_pct=0.0)
        s.s["qty"] = 0.0
        s.s["last_sell_price"] = 51.19
        s.s["last_exit_kind"] = "STOP"
        s.s["sl_low"] = 51.19
        s.step(50.0)                                  # inca scade -> urmareste minimul, nu intra
        self.assertFalse(s._has_open("buy"))
        self.assertEqual(s.s["sl_low"], 50.0)
        s.step(50.8)                                  # +1.6% de la minim 50 -> bounce atins
        self.assertTrue(s._has_open("buy"), "bounce >= prag -> reintra")

    def test_tp_exit_keeps_old_drop_below_sell_rule(self):
        # dupa TP (nu STOP), regula veche ramane: nu recumpara mai sus decat ai vandut
        s = _make_strategy(reentry_sl_bounce_pct=1.5, reentry_drop_pct=2.2, reentry_tolerance_pct=0.0)
        s.s["qty"] = 0.0
        s.s["last_sell_price"] = 100.0
        s.s["last_exit_kind"] = "TP"
        s.step(98.5)                                  # 98.5 > prag 97.8 -> blocat (regula veche)
        self.assertFalse(s._has_open("buy"))
        s.step(97.0)                                  # 97.0 < 97.8 -> reintra
        self.assertTrue(s._has_open("buy"))


class TestTrailingTakeProfit(unittest.TestCase):
    """Trailing-ul rămâne armat după prima depășire a TP-ului."""

    @staticmethod
    def _positioned_strategy(**overrides):
        params = dict(
            takeprofit_pct=5.0,
            tp_trend_hold=True,
            tp_trail_pct=3.0,
            dca_drop_pct=2.0,
        )
        params.update(overrides)
        s = _make_strategy(**params)
        s.s["qty"] = 1.0
        s.s["cost"] = 100.0
        s.s["spent"] = 100.0
        s.s["entry_price"] = 100.0
        s.s["last_buy_price"] = 100.0
        return s

    def test_pullback_below_tp_after_arming_still_exits(self):
        s = self._positioned_strategy()

        s.step(105.5)       # depășește TP=105 și armează trailing-ul
        self.assertEqual(s.s["trail_peak"], 105.5)
        self.assertFalse(s._has_open("sell"))

        s.step(102.0)       # pullback 3.32%; este sub TP, dar trailing-ul e deja armat
        sell = s._find_open("sell")
        self.assertIsNotNone(sell, "trailing-ul armat trebuie să iasă și după căderea sub TP")
        self.assertEqual(sell["kind"], "TP")

    def test_trailing_exit_does_not_open_dca_in_same_tick(self):
        s = self._positioned_strategy(dca_drop_pct=2.0)
        s.step(105.5)
        # Face pragul DCA eligibil simultan cu pullback-ul trailing. O ieșire și o
        # cumpărare în același tick s-ar contrazice și ar crește expunerea accidental.
        s.s["last_buy_price"] = 110.0

        s.step(102.0)

        self.assertTrue(s._has_open("sell"))
        self.assertFalse(s._has_open("buy"))

    def test_adaptive_trailing_floor_never_moves_down_when_volatility_widens(self):
        s = self._positioned_strategy(tp_trail_adaptive=True)

        with patch.object(s, "_effective_trail_pct", side_effect=[1.5, 3.0]):
            s.step(106.0)      # floor initial: 104.41
            protected = s.s["trail_stop"]
            self.assertAlmostEqual(protected, 104.41)

            # Volatilitatea creste si trailing-ul adaptiv s-ar largi la 3%.
            # Floor-ul deja castigat nu trebuie coborat la 102.82.
            s.step(104.0)

        self.assertEqual(s.s["trail_stop"], protected)
        sell = s._find_open("sell")
        self.assertIsNotNone(sell, "floor-ul ratchetat trebuie sa declanseze iesirea")
        self.assertTrue(sell["market"])
        self.assertEqual(sell["kind"], "TP")

    def test_profit_floor_blocks_gap_exit_below_break_even(self):
        s = self._positioned_strategy(
            stop_loss_pct=12.5,
            tp_trail_profit_floor_pct=1.0,
        )
        s.step(105.5)       # armează trailing-ul
        s.step(95.0)        # gap sub break-even, dar încă deasupra hard stop-ului

        self.assertIsNone(s._find_open("sell"))
        self.assertFalse(s._has_open("buy"))

    def test_profit_floor_exits_on_recovery_still_below_trail_stop(self):
        s = self._positioned_strategy(
            stop_loss_pct=12.5,
            tp_trail_profit_floor_pct=1.0,
        )
        s.step(105.5)
        s.step(95.0)
        s.step(101.2)       # referința MARKET 101.10 >= floor; sub trail-stop 102.335
        sell = s._find_open("sell")
        self.assertIsNotNone(sell)
        self.assertTrue(sell["market"])
        self.assertEqual(sell["kind"], "TP")
        self.assertGreaterEqual(sell["price"], 101.0)

    def test_hard_stop_exits_market_after_profit_floor_block(self):
        s = self._positioned_strategy(
            stop_loss_pct=12.5,
            tp_trail_profit_floor_pct=1.0,
        )
        s.step(105.5)
        s.step(95.0)
        self.assertIsNone(s._find_open("sell"))

        s.step(87.0)        # sub avg*(1-12.5%): STOP MARKET indiferent de profit
        sell = s._find_open("sell")
        self.assertIsNotNone(sell)
        self.assertTrue(sell["market"])
        self.assertEqual(sell["kind"], "STOP")

    def test_zero_profit_floor_preserves_market_trailing(self):
        s = self._positioned_strategy(tp_trail_profit_floor_pct=0.0)
        s.step(105.5)
        s.step(95.0)
        sell = s._find_open("sell")
        self.assertIsNotNone(sell)
        self.assertTrue(sell["market"])


class TestProgressiveDcaSpacing(unittest.TestCase):
    def test_completed_buys_widen_the_next_dca_threshold(self):
        s = _make_strategy(
            dca_drop_pct=2.0,
            dca_spacing_growth_pct=0.5,
            reentry_tolerance_pct=0.0,
        )
        s.s.update({
            "qty": 1.0,
            "cost": 100.0,
            "spent": 100.0,
            "entry_price": 100.0,
            "last_buy_price": 100.0,
            "dca_buys": 2,
        })

        s.step(97.5)  # prag progresiv = 2% + 2×0,5% = 3%; încă nu cumpără
        self.assertFalse(s._has_open("buy"))
        s.step(97.0)
        self.assertTrue(s._has_open("buy"))


class TestPaperMarketReconciliation(unittest.TestCase):
    def test_limit_buy_waits_until_observed_price_reaches_limit(self):
        s = _make_strategy(entry_discount_pct=1.0)
        with patch.object(strat, "notify"):
            s.step(100.0)
            order = s._find_open("buy")
            self.assertEqual(order["price"], 99.0)

            s.reconcile(101.0)
            self.assertTrue(s._has_open("buy"))
            self.assertEqual(s.s["qty"], 0.0)

            s.reconcile(98.5)

        self.assertFalse(s._has_open("buy"))
        self.assertGreater(s.s["qty"], 0.0)
        self.assertEqual(s.s["last_buy_price"], 99.0)

    def test_stop_market_sell_fills_at_observed_price_during_drop(self):
        s = _make_strategy(stop_loss_pct=10.0)
        s.s.update({
            "qty": 1.0, "cost": 100.0, "spent": 100.0,
            "entry_price": 100.0, "last_buy_price": 100.0,
        })

        with patch.object(strat, "notify"):
            s.step(80.0)
            order = s._find_open("sell")
            self.assertIsNotNone(order)
            self.assertTrue(order["market"])
            self.assertGreater(order["price"], 70.0)

            s.reconcile(70.0)

        self.assertFalse(s._has_open("sell"))
        self.assertEqual(s.s["qty"], 0.0)
        self.assertEqual(s.s["last_sell_price"], 70.0)
        self.assertLess(s.s["realized_net"], 0.0)


class TestFillAccounting(unittest.TestCase):
    """Cost basis și fees rămân corecte când ieșirea se face în tranșe."""

    def test_two_partial_sells_reduce_cost_and_charge_each_fee_once(self):
        s = _make_strategy()
        buy = {"side": "buy", "kind": "ENTRY", "amount": 200.0}
        with patch.object(strat, "notify"):
            s._apply_fill(buy, vol=2.0, price=100.0, fee=0.52)

            self.assertAlmostEqual(s.s["qty"], 2.0)
            self.assertAlmostEqual(s.s["cost"], 200.0)
            self.assertAlmostEqual(s.s["realized_net"], -0.52)

            sell = {"side": "sell", "kind": "TP"}
            s._apply_fill(sell, vol=1.0, price=110.0, fee=0.286)
            self.assertAlmostEqual(s.s["qty"], 1.0)
            self.assertAlmostEqual(s.s["cost"], 100.0)
            self.assertAlmostEqual(s._avg(), 100.0)

            s._apply_fill(sell, vol=1.0, price=120.0, fee=0.312)
            self.assertAlmostEqual(s.s["qty"], 0.0)
            self.assertAlmostEqual(s.s["cost"], 0.0)
            self.assertAlmostEqual(s.s["realized_gross"], 30.0)
            self.assertAlmostEqual(s.s["fees_total"], 1.118)
            self.assertAlmostEqual(s.s["realized_net"], 28.882)


if __name__ == "__main__":
    unittest.main()

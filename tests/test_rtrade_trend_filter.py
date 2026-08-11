"""rtrade _trend_too_strong: filtru de trend care face bot-ul de spread sa stea deoparte
cand activul trend-uieste clar (|gradient_recent| > K*epsilon). Fail-OPEN + kill-switch.
cacheManager e injectat fake in sys.modules (import lazy in _trend_too_strong)."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import rtrade


class TrendFilterTest(unittest.TestCase):
    def setUp(self):
        self._en = rtrade.RTRADE_TREND_FILTER_ENABLED
        self._k = rtrade.RTRADE_TREND_FILTER_K
        self._cm = sys.modules.get("cacheManager")
        rtrade.RTRADE_TREND_FILTER_ENABLED = True
        rtrade.RTRADE_TREND_FILTER_K = 2.0

    def tearDown(self):
        rtrade.RTRADE_TREND_FILTER_ENABLED = self._en
        rtrade.RTRADE_TREND_FILTER_K = self._k
        if self._cm is not None:
            sys.modules["cacheManager"] = self._cm
        else:
            sys.modules.pop("cacheManager", None)

    def _fake_cm(self, dyn):
        m = types.ModuleType("cacheManager")

        class Mgr:
            def get_instant_trend_for_window(self, sym, w):
                return dyn
        m.get_short_trend_manager = lambda: Mgr()
        sys.modules["cacheManager"] = m

    def test_disabled_returns_false(self):
        rtrade.RTRADE_TREND_FILTER_ENABLED = False
        self._fake_cm({"gradient_recent": 100.0, "epsilon": 0.001})   # ar fi trend f. clar
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))

    def test_strong_trend_blocks(self):
        self._fake_cm({"gradient_recent": 0.5, "epsilon": 0.1})       # 0.5 > 2*0.1=0.2
        self.assertTrue(rtrade._trend_too_strong("TAOUSDC"))

    def test_weak_trend_allows(self):
        self._fake_cm({"gradient_recent": 0.15, "epsilon": 0.1})      # 0.15 < 0.2
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))

    def test_none_dyn_fail_open(self):
        self._fake_cm(None)                                           # trend indisponibil
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))

    def test_exception_fail_open(self):
        m = types.ModuleType("cacheManager")

        def boom():
            raise RuntimeError("cm down")
        m.get_short_trend_manager = boom
        sys.modules["cacheManager"] = m
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))         # eroare -> nu blocheaza

    def test_zero_epsilon_not_strong(self):
        self._fake_cm({"gradient_recent": 0.0, "epsilon": 0.0})       # piata plata -> nu blocheaza
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))


class FollowupForceTest(unittest.TestCase):
    """Followup (flip dupa fill): force=piata DOAR daca trendul nu e advers. Advers:
    SELL in declin / BUY in urcus -> False (limita rabdatoare, nu dumpeaza la piata)."""
    def setUp(self):
        self._en = rtrade.RTRADE_TREND_FILTER_ENABLED
        self._k = rtrade.RTRADE_TREND_FILTER_K
        self._cm = sys.modules.get("cacheManager")
        rtrade.RTRADE_TREND_FILTER_ENABLED = True
        rtrade.RTRADE_TREND_FILTER_K = 2.0

    def tearDown(self):
        rtrade.RTRADE_TREND_FILTER_ENABLED = self._en
        rtrade.RTRADE_TREND_FILTER_K = self._k
        if self._cm is not None:
            sys.modules["cacheManager"] = self._cm
        else:
            sys.modules.pop("cacheManager", None)

    def _fake_cm(self, dyn):
        m = types.ModuleType("cacheManager")

        class Mgr:
            def get_instant_trend_for_window(self, sym, w):
                return dyn
        m.get_short_trend_manager = lambda: Mgr()
        sys.modules["cacheManager"] = m

    def test_sell_in_downtrend_patient(self):
        self._fake_cm({"gradient_recent": -0.5, "epsilon": 0.1})      # declin clar
        self.assertFalse(rtrade._followup_force("TAOUSDC", "SELL"))   # NU vinde la piata

    def test_sell_in_uptrend_force(self):
        self._fake_cm({"gradient_recent": 0.5, "epsilon": 0.1})       # urcus -> favorabil pt SELL
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))

    def test_buy_in_uptrend_patient(self):
        self._fake_cm({"gradient_recent": 0.5, "epsilon": 0.1})       # urcus clar
        self.assertFalse(rtrade._followup_force("TAOUSDC", "BUY"))    # NU cumpara disperat la piata

    def test_buy_in_downtrend_force(self):
        self._fake_cm({"gradient_recent": -0.5, "epsilon": 0.1})      # declin -> favorabil pt BUY
        self.assertTrue(rtrade._followup_force("TAOUSDC", "BUY"))

    def test_weak_trend_forces(self):
        self._fake_cm({"gradient_recent": 0.15, "epsilon": 0.1})      # slab -> flip imediat ok
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))

    def test_disabled_or_unavailable_forces(self):
        rtrade.RTRADE_TREND_FILTER_ENABLED = False
        self._fake_cm({"gradient_recent": -0.5, "epsilon": 0.1})
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))    # kill-switch off -> ca inainte
        rtrade.RTRADE_TREND_FILTER_ENABLED = True
        self._fake_cm(None)
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))    # indisponibil -> force (fail-open)


if __name__ == "__main__":
    unittest.main(verbosity=2)

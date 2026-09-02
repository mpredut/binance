"""rtrade _trend_too_strong: the trend filter that makes the spread bot stand aside
when the asset is clearly trending (|gradient_recent| > K*epsilon). Fail-OPEN plus a kill switch.
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

    def test_snapshot_strength_cases(self):
        cases = (
            ("strong", {"gradient_recent": 0.5, "epsilon": 0.1}, True),
            ("weak", {"gradient_recent": 0.15, "epsilon": 0.1}, False),
            ("unavailable", None, False),
            ("flat", {"gradient_recent": 0.0, "epsilon": 0.0}, False),
        )
        for label, snapshot, expected in cases:
            with self.subTest(case=label):
                self._fake_cm(snapshot)
                self.assertEqual(rtrade._trend_too_strong("TAOUSDC"), expected)

    def test_exception_fail_open(self):
        m = types.ModuleType("cacheManager")

        def boom():
            raise RuntimeError("cm down")
        m.get_short_trend_manager = boom
        sys.modules["cacheManager"] = m
        self.assertFalse(rtrade._trend_too_strong("TAOUSDC"))         # An error -> it does not block.

class FollowupForceTest(unittest.TestCase):
    """Follow-up (flip after a fill): force=market ONLY if the trend is not adverse. Adverse:
    A SELL into a decline / a BUY into a rise -> False (a patient limit, it does not dump into the market)."""
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

    def test_directional_force_policy(self):
        cases = (
            ("sell-down", "SELL", -0.5, False),
            ("sell-up", "SELL", 0.5, True),
            ("buy-up", "BUY", 0.5, False),
            ("buy-down", "BUY", -0.5, True),
            ("weak-sell", "SELL", 0.15, True),
        )
        for label, side, gradient, expected in cases:
            with self.subTest(case=label):
                self._fake_cm({"gradient_recent": gradient, "epsilon": 0.1})
                self.assertEqual(rtrade._followup_force("TAOUSDC", side), expected)

    def test_disabled_or_unavailable_forces(self):
        rtrade.RTRADE_TREND_FILTER_ENABLED = False
        self._fake_cm({"gradient_recent": -0.5, "epsilon": 0.1})
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))    # kill switch off -> as before
        rtrade.RTRADE_TREND_FILTER_ENABLED = True
        self._fake_cm(None)
        self.assertTrue(rtrade._followup_force("TAOUSDC", "SELL"))    # indisponibil -> force (fail-open)


class MarketRegimeTest(unittest.TestCase):
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
            def get_instant_trend_for_window(self, _sym, _window):
                return dyn
        m.get_short_trend_manager = lambda: Mgr()
        sys.modules["cacheManager"] = m

    def test_regimes_use_signed_gradient_and_adaptive_noise(self):
        for gradient, expected in ((0.5, "bull"), (-0.5, "bear"),
                                   (0.15, "sideways")):
            with self.subTest(expected=expected):
                self._fake_cm({"gradient_recent": gradient, "epsilon": 0.1})
                self.assertEqual(
                    rtrade._market_regime_decision("TAOUSDC").regime, expected)

    def test_missing_signal_is_unknown(self):
        self._fake_cm(None)
        self.assertEqual(
            rtrade._market_regime_decision("TAOUSDC").regime, "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)

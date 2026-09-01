#!/usr/bin/env python3
"""Test per-asset configuration isolation and discovery in the unified T212 bot.

Two assets in one process must retain distinct settings without colliding through
``os.environ``.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipo_common import float_env, parse_dotenv  # noqa: E402
from strategy import StratParams  # noqa: E402
from t212_bot import discover_assets  # noqa: E402

NVDA = """
T212_TICKER=NVDA_US_EQ
YAHOO_SYMBOL=NVDA
STRATEGY_MODE=avg_tp
STRAT_ENTRY_PCT=20
STRAT_DCA_PCT=10
STRAT_TAKEPROFIT_PCT=1.5
STRAT_CURRENCY=EUR
STRAT_REENTRY_DROP_PCT=1.5
STRAT_CHECK_MINUTES=0.4  # comentariu inline
STRAT_ENTRY_DISCOUNT_PCT=0.2
STRAT_DCA_DROP_PCT=2
STRAT_MAX_DCA_BUYS=3
STRAT_MAX_BUDGET=500
STRAT_FX_FEE_PCT=0.15
STRAT_STOP_LOSS_PCT=20
STRAT_ORDER_TTL_MIN=10
STRAT_REENTRY_TOLERANCE_PCT=0
STRAT_LOSS_ALERT_STEP=1
STRAT_LADDER_MIN_FREE=6
STRAT_SL_REBUY_ENABLED=false
STRAT_SL_REBUY_BOUNCE_PCT=1.2
STRAT_DCA_TREND_GATE_PCT=0
STRAT_TRAIL_PCT=0
STRAT_TRAIL_MIN_PROFIT_PCT=5
"""

SPCX = """
T212_TICKER=SPCX_US_EQ
YAHOO_SYMBOL=SPCX
STRATEGY_MODE=avg_tp
STRAT_ENTRY_PCT=30
STRAT_DCA_PCT=10
STRAT_TAKEPROFIT_PCT=5.0
STRAT_CURRENCY=EUR
STRAT_REENTRY_DROP_PCT=2
STRAT_CHECK_MINUTES=1
STRAT_ENTRY_DISCOUNT_PCT=0.2
STRAT_DCA_DROP_PCT=2
STRAT_MAX_DCA_BUYS=3
STRAT_MAX_BUDGET=400
STRAT_FX_FEE_PCT=0.15
STRAT_STOP_LOSS_PCT=20
STRAT_ORDER_TTL_MIN=10
STRAT_REENTRY_TOLERANCE_PCT=0
STRAT_LOSS_ALERT_STEP=1
STRAT_LADDER_MIN_FREE=6
STRAT_SL_REBUY_ENABLED=false
STRAT_SL_REBUY_BOUNCE_PCT=1.2
STRAT_DCA_TREND_GATE_PCT=0
STRAT_TRAIL_PCT=0
STRAT_TRAIL_MIN_PROFIT_PCT=5
"""


class TestParseDotenv(unittest.TestCase):
    def test_returns_dict_fara_environ(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("FOO_UNIQ_123=bar\n")
            path = f.name
        try:
            cfg = parse_dotenv(path)
            self.assertEqual(cfg["FOO_UNIQ_123"], "bar")
            self.assertNotIn("FOO_UNIQ_123", os.environ, "parse_dotenv must NOT touch os.environ")
        finally:
            os.unlink(path)

    def test_curata_comentarii_inline(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("X=0.4  # check minutes\nY=\"quoted\"\n")
            path = f.name
        try:
            cfg = parse_dotenv(path)
            self.assertEqual(cfg["X"], "0.4")
            self.assertEqual(cfg["Y"], "quoted")
        finally:
            os.unlink(path)


class TestFloatEnvDict(unittest.TestCase):
    def test_din_dict(self):
        self.assertEqual(float_env("A", {"A": "12.5"}), 12.5)
        self.assertIsNone(float_env("LIPSA", {"A": "1"}))

    def test_implicit_environ(self):
        os.environ["TST_FLT_9"] = "3.3"
        try:
            self.assertEqual(float_env("TST_FLT_9"), 3.3)
        finally:
            del os.environ["TST_FLT_9"]


class TestIzolareParams(unittest.TestCase):
    def test_doua_active_parametri_diferiti(self):
        """Inima redesign-ului: from_env(dict) izoleaza — fara coliziune pe environ."""
        nv = parse_dotenv(_tmp(NVDA))
        sp = parse_dotenv(_tmp(SPCX))
        pn = StratParams.from_env(nv)
        ps = StratParams.from_env(sp)
        self.assertEqual(pn.entry_amount, 100.0)
        self.assertEqual(ps.entry_amount, 120.0)
        self.assertEqual(pn.takeprofit_pct, 1.5)
        self.assertEqual(ps.takeprofit_pct, 5.0)
        self.assertEqual(pn.yahoo_sym, "NVDA")
        self.assertEqual(ps.yahoo_sym, "SPCX")
        self.assertEqual(pn.reentry_drop_pct, 1.5)
        self.assertEqual(ps.reentry_drop_pct, 2.0)

    def test_inline_comment_in_check_minutes(self):
        pn = StratParams.from_env(parse_dotenv(_tmp(NVDA)))
        self.assertEqual(pn.check_minutes, 0.4)  # not 'NaN' from the inline comment

    def test_missing_environment_fails_instead_of_using_defaults(self):
        with self.assertRaisesRegex(ValueError, "mandatory setting"):
            StratParams.from_env({})


class TestDiscover(unittest.TestCase):
    def test_gaseste_config_active(self):
        d = tempfile.mkdtemp()
        for n in ("nvda", "spcx", "rgnt"):
            open(os.path.join(d, f"config.{n}.env"), "w").close()
        open(os.path.join(d, ".env"), "w").close()  # NU e activ
        names = [n for n, _ in discover_assets(d)]
        self.assertEqual(names, ["nvda", "rgnt", "spcx"])  # sortat, fara .env


def _tmp(content: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    f.write(content)
    f.close()
    return f.name


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
Teste pentru extragerea in variabile de mediu a constantelor hardcodate din
rtrade.py (23 iul, cerere user: "inventariaza toti parametrii vizi/constante
si scoate-i in fisiere de config" — un bot pe rand: tradeall.py,
monitortrades.py, apoi rtrade.py).

IMPORTANT (siguranta): rtrade.py rula INAINTE bot.run() (bucla LIVE infinita,
ordine reale) necondiionat la IMPORT, fara "if __name__ == '__main__':". Ca
parte a acestei extrageri, blocul de pornire a fost mutat sub acel guard —
verificat ca nimic altceva nu importa rtrade.py (grep) si ca flota_start.sh il
ruleaza direct (`python rtrade.py`), deci comportamentul de PRODUCTIE ramane
identic. Acest fisier de test se bazeaza pe acel guard: importul de mai jos
NU trebuie sa porneasca vreo bucla sau sa faca vreun apel de retea.

Acoperire:
  - Valorile IMPLICITE (fara env setat) reproduc EXACT vechile constante
    hardcodate — inclusiv asimetria BUY vs SELL (decay, ore), pastrata intentionat.
  - Setarea variabilei de mediu chiar schimba valoarea constantei.
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import rtrade as rt

ENV_VARS = [
    "RTRADE_WAIT_FOR_ORDER_SEC", "RTRADE_MIN_ADJUSTMENT_PCT", "RTRADE_QTY",
    "RTRADE_DEFAULT_ADJUSTMENT_PCT", "RTRADE_INITIAL_SPREAD_PCT",
    "RTRADE_BUY_DECAY_PCT", "RTRADE_SELL_DECAY_PCT",
    "RTRADE_BUY_DESPERATE_HOURS_BASE", "RTRADE_SELL_DESPERATE_HOURS_BASE",
    "RTRADE_DESPERATE_SAFEBACK_SEC", "RTRADE_BUY_NORMAL_HOURS",
    "RTRADE_SELL_NORMAL_HOURS", "RTRADE_FOLLOWUP_OFFSET_PCT",
    "RTRADE_FOLLOWUP_HOURS", "RTRADE_BAD_DAY_TOLERANCE_PCT",
    "RTRADE_BAD_DAY_MULTIPLIER", "RTRADE_ZERO_EPSILON", "RTRADE_MAX_FAILURES",
]


class TestModuleImportIsSafe(unittest.TestCase):
    """Guard de siguranta: import rtrade NU trebuie sa expuna bot.run() la
    nivel de modul (altfel testul de mai sus ar fi ramas blocat / ar fi facut
    apeluri de retea la colectarea testelor)."""

    def test_bot_not_instantiated_at_import(self):
        self.assertFalse(hasattr(rt, "bot"), "bot.run() nu trebuie sa porneasca la import (doar sub __main__)")


class TestDefaultsMatchOldHardcodedValues(unittest.TestCase):

    def test_wait_for_order(self):
        self.assertEqual(rt.WAIT_FOR_ORDER, 32)

    def test_min_adjustment_percent(self):
        self.assertEqual(rt.MIN_adjustment_percent, 0.01)

    def test_qty(self):
        self.assertEqual(rt.RTRADE_QTY, 100)

    def test_default_adjustment_percent(self):
        expected = round(__import__("utils").calculate_difference_percent(60000, 60000 - 380) / 100, 4)
        self.assertEqual(rt.DEFAULT_ADJUSTMENT_PERCENT, expected)

    def test_initial_spread(self):
        self.assertEqual(rt.RTRADE_INITIAL_SPREAD_PCT, 0.1)

    def test_decay_asymmetric_buy_vs_sell(self):
        self.assertEqual(rt.RTRADE_BUY_DECAY_PCT, 0.005)
        self.assertEqual(rt.RTRADE_SELL_DECAY_PCT, 0.01)
        self.assertNotEqual(rt.RTRADE_BUY_DECAY_PCT, rt.RTRADE_SELL_DECAY_PCT,
                             "asimetria BUY/SELL din codul original trebuie pastrata")

    def test_desperate_hours_base_asymmetric(self):
        self.assertEqual(rt.RTRADE_BUY_DESPERATE_HOURS_BASE, 0.3)
        self.assertEqual(rt.RTRADE_SELL_DESPERATE_HOURS_BASE, 0.23)

    def test_desperate_safeback_sec(self):
        self.assertEqual(rt.RTRADE_DESPERATE_SAFEBACK_SEC, 1 * 3600 + 60)

    def test_normal_hours_asymmetric(self):
        self.assertEqual(rt.RTRADE_BUY_NORMAL_HOURS, 16)
        self.assertEqual(rt.RTRADE_SELL_NORMAL_HOURS, 12)

    def test_followup(self):
        self.assertEqual(rt.RTRADE_FOLLOWUP_OFFSET_PCT, 0.01)
        self.assertEqual(rt.RTRADE_FOLLOWUP_HOURS, 2.7)

    def test_bad_day(self):
        self.assertEqual(rt.RTRADE_BAD_DAY_TOLERANCE_PCT, 0.1)
        self.assertEqual(rt.RTRADE_BAD_DAY_MULTIPLIER, 1.7)

    def test_zero_epsilon(self):
        self.assertEqual(rt.RTRADE_ZERO_EPSILON, 0.0001)

    def test_max_failures(self):
        self.assertEqual(rt.RTRADE_MAX_FAILURES, 10)


class TestEnvOverrideActuallyWorks(unittest.TestCase):

    def tearDown(self):
        for v in ENV_VARS:
            os.environ.pop(v, None)
        importlib.reload(rt)

    def test_override_qty(self):
        os.environ["RTRADE_QTY"] = "250"
        importlib.reload(rt)
        self.assertEqual(rt.RTRADE_QTY, 250)

    def test_override_buy_decay(self):
        os.environ["RTRADE_BUY_DECAY_PCT"] = "0.02"
        importlib.reload(rt)
        self.assertEqual(rt.RTRADE_BUY_DECAY_PCT, 0.02)

    def test_override_bad_day_multiplier(self):
        os.environ["RTRADE_BAD_DAY_MULTIPLIER"] = "2.0"
        importlib.reload(rt)
        self.assertEqual(rt.RTRADE_BAD_DAY_MULTIPLIER, 2.0)

    def test_override_max_failures(self):
        os.environ["RTRADE_MAX_FAILURES"] = "3"
        importlib.reload(rt)
        self.assertEqual(rt.RTRADE_MAX_FAILURES, 3)

    def test_override_default_adjustment_pct(self):
        os.environ["RTRADE_DEFAULT_ADJUSTMENT_PCT"] = "0.02"
        importlib.reload(rt)
        self.assertEqual(rt.DEFAULT_ADJUSTMENT_PERCENT, 0.02)


if __name__ == "__main__":
    unittest.main()

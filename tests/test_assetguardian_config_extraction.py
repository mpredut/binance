"""
Teste pentru extragerea in variabile de mediu a constantelor hardcodate din
assetguardian.py (23 iul, cerere user: "inventariaza toti parametrii
vizi/constante si scoate-i in fisiere de config" — ultima etapa dupa
tradeall.py, monitortrades.py si rtrade.py).

assetguardian.py avea deja un guard corect ("if __name__ == '__main__':
run_forever()") — importul e sigur din start, spre deosebire de rtrade.py.

Acoperire:
  - Valorile IMPLICITE (fara env setat) reproduc EXACT vechile constante
    hardcodate — zero schimbare de comportament daca nu se seteaza nimic.
  - Setarea variabilei de mediu chiar schimba valoarea constantei.
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import assetguardian as ag

ENV_VARS = [
    "AG_CHECK_INTERVAL_SEC", "AG_TARGET_GROWTH_PCT", "AG_TARGET_DROP_PCT",
    "AG_REFERENCE_MINUTES_BACK", "AG_BUY_USE_CASH_RATIO",
]


class TestModuleImportIsSafe(unittest.TestCase):
    """Guard preexistent: import assetguardian NU trebuie sa porneasca run_forever()."""

    def test_run_forever_not_called_at_import(self):
        # daca run_forever() ar fi rulat la import, acest test nu s-ar mai executa
        # (bucla e infinita) — simpla ajungere aici confirma ca guard-ul e intact.
        self.assertTrue(hasattr(ag, "run_forever"))


class TestDefaultsMatchOldHardcodedValues(unittest.TestCase):

    def test_check_interval(self):
        self.assertEqual(ag.CHECK_INTERVAL_SECONDS, 0.9 * 60)

    def test_target_growth_percent(self):
        self.assertEqual(ag.TARGET_GROWTH_PERCENT, 100.0)

    def test_target_drop_percent(self):
        self.assertEqual(ag.TARGET_DROP_PERCENT, 7.0)

    def test_reference_minutes_back(self):
        self.assertEqual(ag.ASSET_REFERENCE_MINUTES_BACK_DEFAULT, 24 * 60)

    def test_buy_use_cash_ratio(self):
        self.assertEqual(ag.BUY_USE_CASH_RATIO, 0.995)


class TestEnvOverrideActuallyWorks(unittest.TestCase):

    def tearDown(self):
        for v in ENV_VARS:
            os.environ.pop(v, None)
        importlib.reload(ag)

    def test_override_check_interval(self):
        os.environ["AG_CHECK_INTERVAL_SEC"] = "30"
        importlib.reload(ag)
        self.assertEqual(ag.CHECK_INTERVAL_SECONDS, 30)

    def test_override_target_growth_percent(self):
        os.environ["AG_TARGET_GROWTH_PCT"] = "15"
        importlib.reload(ag)
        self.assertEqual(ag.TARGET_GROWTH_PERCENT, 15)

    def test_override_target_drop_percent(self):
        os.environ["AG_TARGET_DROP_PCT"] = "5"
        importlib.reload(ag)
        self.assertEqual(ag.TARGET_DROP_PERCENT, 5)

    def test_override_reference_minutes_back(self):
        os.environ["AG_REFERENCE_MINUTES_BACK"] = "60"
        importlib.reload(ag)
        self.assertEqual(ag.ASSET_REFERENCE_MINUTES_BACK_DEFAULT, 60)

    def test_override_buy_use_cash_ratio(self):
        os.environ["AG_BUY_USE_CASH_RATIO"] = "0.5"
        importlib.reload(ag)
        self.assertEqual(ag.BUY_USE_CASH_RATIO, 0.5)


if __name__ == "__main__":
    unittest.main()

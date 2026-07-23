"""
Teste pentru extragerea in variabile de mediu a constantelor hardcodate din
tradeall.py (23 iul, cerere user: "inventariaza toti parametrii vizi/constante
si scoate-i in fisiere de config" — un bot pe rand, incepand cu tradeall.py).

Acoperire:
  - Valorile IMPLICITE (fara env setat) reproduc EXACT vechile constante
    hardcodate — zero schimbare de comportament daca nu se seteaza nimic.
  - Setarea variabilei de mediu chiar schimba valoarea constantei (mecanismul
    de override functioneaza, nu doar aparent).

Constantele sunt citite O SINGURA DATA la import (acelasi tipar ca
KALMAN_PRIMARY_SYMBOLS, deja existent) — testele de override reincarca
modulul (importlib.reload) cu env-ul deja setat, ca sa surprinda valoarea noua.
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import tradeall as ta
import utils as u

ENV_VARS = [
    "TRADEALL_TREND_OLD_HOURS", "TRADEALL_PRICE_CHANGE_THRESHOLD_PCT",
    "TRADEALL_PRICE_CHANGE_THRESHOLD_BIG_PCT", "TRADEALL_TREND_MIN_VALIDATED_SEC",
    "TRADEALL_TREND_MIN_VALIDATED_CONFIRMS", "TRADEALL_TREND_CONSISTENT_CONFIRMS",
    "TRADEALL_TREND_UNIFORM_RATE", "TRADEALL_SLOPE_EXTREME_THRESHOLD",
    "TRADEALL_FIRE_MIN_RETRY_MINUTES", "TRADEALL_FIRE_MAX_PER_TREND",
]


class TestDefaultsMatchOldHardcodedValues(unittest.TestCase):
    """Valorile implicite (din tradeall_config.env, care e mereu incarcat la
    import) trebuie sa fie IDENTICE cu vechile constante hardcodate (regresie
    de comportament = bug critic aici). Nota: NU putem verifica aici ca
    os.environ e "curat" — in unittest discover, tradeall.py poate fi deja
    importat (si deci load_dotenv() deja rulat) de un alt fisier de test mai
    devreme in acelasi proces; asta nu schimba faptul ca valorile trebuie sa
    corespunda exact vechilor constante."""

    def test_trend_to_be_old_seconds(self):
        self.assertEqual(ta.TREND_TO_BE_OLD_SECONDS, 60 * 60 * 1.9)

    def test_price_change_threshold(self):
        expected = u.calculate_difference_percent(60000, 60000 - 310)
        self.assertAlmostEqual(ta.PRICE_CHANGE_THRESHOLD_EUR, expected, places=6)

    def test_price_change_threshold_big(self):
        expected = u.calculate_difference_percent(97000, 95000 - 377)
        self.assertAlmostEqual(ta.PRICE_CHANGE_THRESHOLD_BIG_EUR, expected, places=6)

    def test_trend_min_validated(self):
        self.assertEqual(ta.TREND_MIN_VALIDATED_SECONDS, 30)
        self.assertEqual(ta.TREND_MIN_VALIDATED_CONFIRMS, 3)

    def test_trend_consistent_confirms(self):
        self.assertEqual(ta.TREND_CONSISTENT_CONFIRMS, 8 * 3)

    def test_trend_uniform_rate(self):
        self.assertEqual(ta.TREND_UNIFORM_RATE_THRESHOLD, 0.08)

    def test_slope_extreme_threshold(self):
        self.assertEqual(ta.SLOPE_EXTREME_THRESHOLD, 5.1)

    def test_fire_cooldown_defaults(self):
        self.assertEqual(ta.FIRE_MIN_RETRY_INTERVAL_SEC, 6 * 60)
        self.assertEqual(ta.FIRE_MAX_PER_TREND, 3)


class TestEnvOverrideActuallyWorks(unittest.TestCase):
    """Setarea variabilei de mediu ÎNAINTE de (re)import chiar schimba valoarea —
    nu doar teoretic disponibila, ci efectiv folosita."""

    def tearDown(self):
        for v in ENV_VARS:
            os.environ.pop(v, None)
        importlib.reload(ta)   # readuce modulul la starea implicita pt urmatoarele teste

    def test_override_slope_extreme_threshold(self):
        os.environ["TRADEALL_SLOPE_EXTREME_THRESHOLD"] = "9.9"
        importlib.reload(ta)
        self.assertEqual(ta.SLOPE_EXTREME_THRESHOLD, 9.9)

    def test_override_fire_max_per_trend(self):
        os.environ["TRADEALL_FIRE_MAX_PER_TREND"] = "7"
        importlib.reload(ta)
        self.assertEqual(ta.FIRE_MAX_PER_TREND, 7)

    def test_override_fire_min_retry_minutes(self):
        os.environ["TRADEALL_FIRE_MIN_RETRY_MINUTES"] = "1"
        importlib.reload(ta)
        self.assertEqual(ta.FIRE_MIN_RETRY_INTERVAL_SEC, 60)

    def test_override_trend_consistent_confirms(self):
        os.environ["TRADEALL_TREND_CONSISTENT_CONFIRMS"] = "48"
        importlib.reload(ta)
        self.assertEqual(ta.TREND_CONSISTENT_CONFIRMS, 48)

    def test_override_trend_old_hours(self):
        os.environ["TRADEALL_TREND_OLD_HOURS"] = "3.0"
        importlib.reload(ta)
        self.assertEqual(ta.TREND_TO_BE_OLD_SECONDS, 3.0 * 3600)


if __name__ == "__main__":
    unittest.main()

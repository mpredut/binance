"""
Teste CONSOLIDATE pt extragerea constantelor in fisiere de config (28 iul —
unificate din 4 fisiere aproape identice: test_{tradeall,monitortrades,rtrade,
assetguardian}_config_extraction.py, ~56 teste sparse -> table-driven aici).

Intentia PASTRATA integral:
  - DEFAULTS: fiecare constanta extrasa are ca default EXACT valoarea veche
    hardcodata (zero schimbare de comportament daca nu modifici configul).
  - OVERRIDE: setarea variabilei de mediu + reload chiar schimba valoarea
    (mecanismul de override functioneaza real, nu doar aparent).
  - Specific per modul: siguranta importului (rtrade/assetguardian NU pornesc
    bucla live la import), cod mort eliminat (monitortrades SYMBOL_PARAMS),
    asimetria BUY/SELL pastrata (rtrade).
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import utils as u
import tradeall as ta
import monitortrades as mt
import rtrade as rt
import assetguardian as ag

# Valori calculate (identice cu ce faceau fisierele originale).
_PC_SMALL = u.calculate_difference_percent(60000, 60000 - 310)
_PC_BIG = u.calculate_difference_percent(97000, 95000 - 377)
_RT_ADJ = round(u.calculate_difference_percent(60000, 60000 - 380) / 100, 4)

# (modul, atribut, valoare_default_asteptata = vechea valoare hardcodata)
DEFAULTS = [
    (ta, "TREND_TO_BE_OLD_SECONDS", 60 * 60 * 1.9),
    (ta, "PRICE_CHANGE_THRESHOLD_EUR", _PC_SMALL),
    (ta, "PRICE_CHANGE_THRESHOLD_BIG_EUR", _PC_BIG),
    (ta, "TREND_MIN_VALIDATED_SECONDS", 30),
    (ta, "TREND_MIN_VALIDATED_CONFIRMS", 3),
    (ta, "TREND_CONSISTENT_CONFIRMS", 8 * 3),
    (ta, "TREND_UNIFORM_RATE_THRESHOLD", 0.08),
    (ta, "SLOPE_EXTREME_THRESHOLD", 5.1),
    (ta, "FIRE_MIN_RETRY_INTERVAL_SEC", 6 * 60),
    (ta, "FIRE_MAX_PER_TREND", 3),
    (mt, "MT_ARE_CLOSE_TOLERANCE_PCT", 1.0),
    (mt, "MT_RECENT_TRADE_BLOCK_SEC", 3 * 60 * 60),
    (mt, "MT_ALL_TRADES_BLOCK_SEC", 1 * 60 * 60),
    (mt, "MT_MAIN_LOOP_SLEEP_SEC", 60 * 0.8),
    (mt, "MT_BUY_PRICE_OFFSET", 0.5),
    (mt, "MT_SELL_SAFEBACK_HOURS", 2),
    (mt, "MT_BUY_SAFEBACK_HOURS", 48),
    (mt, "HARD_TP_PCT", 0.17),
    (mt, "HARD_TP_FRACTION", 0.5),
    (mt, "HARD_TP_COOLDOWN_S", 6 * 3600),
    (mt, "TP_REFERENCE", "last"),
    (rt, "WAIT_FOR_ORDER", 32),
    (rt, "MIN_adjustment_percent", 0.01),
    (rt, "RTRADE_QTY", 100),
    (rt, "DEFAULT_ADJUSTMENT_PERCENT", _RT_ADJ),
    (rt, "RTRADE_INITIAL_SPREAD_PCT", 0.1),
    (rt, "RTRADE_BUY_DECAY_PCT", 0.005),
    (rt, "RTRADE_SELL_DECAY_PCT", 0.01),
    (rt, "RTRADE_BUY_DESPERATE_HOURS_BASE", 0.3),
    (rt, "RTRADE_SELL_DESPERATE_HOURS_BASE", 0.23),
    (rt, "RTRADE_DESPERATE_SAFEBACK_SEC", 1 * 3600 + 60),
    (rt, "RTRADE_BUY_NORMAL_HOURS", 16),
    (rt, "RTRADE_SELL_NORMAL_HOURS", 12),
    (rt, "RTRADE_FOLLOWUP_OFFSET_PCT", 0.01),
    (rt, "RTRADE_FOLLOWUP_HOURS", 2.7),
    (rt, "RTRADE_BAD_DAY_TOLERANCE_PCT", 0.1),
    (rt, "RTRADE_BAD_DAY_MULTIPLIER", 1.7),
    (rt, "RTRADE_ZERO_EPSILON", 0.0001),
    (rt, "RTRADE_MAX_FAILURES", 10),
    (rt, "RTRADE_PAIR_COORDINATOR_ENABLED", True),
    (rt, "RTRADE_PAIR_POLL_SEC", 1),
    (rt, "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", 4),
    (rt, "RTRADE_PAIR_START_INTERVAL_SEC", 8),
    (rt, "RTRADE_PAIR_DIRECTIONS", ("BUY", "SELL")),
    (rt, "RTRADE_FAST_FILL_RATIO", 0.25),
    (rt, "RTRADE_MIN_EDGE_PCT", 0.0115),
    (rt, "RTRADE_SHOCK_HARD_STOP_PCT", 0.04),
    (rt, "RTRADE_HARD_STOP_PCT", 0.08),
    (ag, "CHECK_INTERVAL_SECONDS", 0.9 * 60),
    (ag, "TARGET_GROWTH_PERCENT", 100.0),
    (ag, "TARGET_DROP_PERCENT", 7.0),
    (ag, "ASSET_REFERENCE_MINUTES_BACK_DEFAULT", 24 * 60),
    (ag, "BUY_USE_CASH_RATIO", 0.995),
]

# (modul, env_var, valoare_str, atribut, valoare_asteptata_dupa_override)
OVERRIDES = [
    (ta, "TRADEALL_SLOPE_EXTREME_THRESHOLD", "9.9", "SLOPE_EXTREME_THRESHOLD", 9.9),
    (ta, "TRADEALL_FIRE_MAX_PER_TREND", "7", "FIRE_MAX_PER_TREND", 7),
    (ta, "TRADEALL_FIRE_MIN_RETRY_MINUTES", "1", "FIRE_MIN_RETRY_INTERVAL_SEC", 60),
    (ta, "TRADEALL_TREND_CONSISTENT_CONFIRMS", "48", "TREND_CONSISTENT_CONFIRMS", 48),
    (ta, "TRADEALL_TREND_OLD_HOURS", "3.0", "TREND_TO_BE_OLD_SECONDS", 3.0 * 3600),
    (mt, "MT_ARE_CLOSE_TOLERANCE_PCT", "2.5", "MT_ARE_CLOSE_TOLERANCE_PCT", 2.5),
    (mt, "MT_RECENT_TRADE_BLOCK_HOURS", "5", "MT_RECENT_TRADE_BLOCK_SEC", 5 * 3600),
    (mt, "MT_MAIN_LOOP_SLEEP_SEC", "30", "MT_MAIN_LOOP_SLEEP_SEC", 30),
    (mt, "MT_BUY_PRICE_OFFSET", "1.0", "MT_BUY_PRICE_OFFSET", 1.0),
    (mt, "MT_SELL_SAFEBACK_HOURS", "4", "MT_SELL_SAFEBACK_HOURS", 4),
    (mt, "MT_BUY_SAFEBACK_HOURS", "24", "MT_BUY_SAFEBACK_HOURS", 24),
    (rt, "RTRADE_QTY", "250", "RTRADE_QTY", 250),
    (rt, "RTRADE_BUY_DECAY_PCT", "0.02", "RTRADE_BUY_DECAY_PCT", 0.02),
    (rt, "RTRADE_BAD_DAY_MULTIPLIER", "2.0", "RTRADE_BAD_DAY_MULTIPLIER", 2.0),
    (rt, "RTRADE_MAX_FAILURES", "3", "RTRADE_MAX_FAILURES", 3),
    (rt, "RTRADE_DEFAULT_ADJUSTMENT_PCT", "0.02", "DEFAULT_ADJUSTMENT_PERCENT", 0.02),
    (rt, "RTRADE_PAIR_POLL_SEC", "2.5", "RTRADE_PAIR_POLL_SEC", 2.5),
    (rt, "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", "7", "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", 7),
    (rt, "RTRADE_PAIR_START_INTERVAL_SEC", "3.5", "RTRADE_PAIR_START_INTERVAL_SEC", 3.5),
    (rt, "RTRADE_FAST_FILL_RATIO", "0.5", "RTRADE_FAST_FILL_RATIO", 0.5),
    (ag, "AG_CHECK_INTERVAL_SEC", "30", "CHECK_INTERVAL_SECONDS", 30),
    (ag, "AG_TARGET_GROWTH_PCT", "15", "TARGET_GROWTH_PERCENT", 15),
    (ag, "AG_TARGET_DROP_PCT", "5", "TARGET_DROP_PERCENT", 5),
    (ag, "AG_REFERENCE_MINUTES_BACK", "60", "ASSET_REFERENCE_MINUTES_BACK_DEFAULT", 60),
    (ag, "AG_BUY_USE_CASH_RATIO", "0.5", "BUY_USE_CASH_RATIO", 0.5),
]


class TestConfigDefaults(unittest.TestCase):
    """Fiecare constanta extrasa are ca DEFAULT exact valoarea veche hardcodata."""

    def test_defaults_match_old_hardcoded_values(self):
        for module, attr, expected in DEFAULTS:
            with self.subTest(module=module.__name__, attr=attr):
                actual = getattr(module, attr)
                if isinstance(expected, str):
                    self.assertEqual(actual, expected)
                else:
                    self.assertAlmostEqual(actual, expected, places=6)


class TestConfigOverrides(unittest.TestCase):
    """Setarea variabilei de mediu + reload chiar schimba valoarea constantei."""

    def test_env_overrides_take_effect(self):
        for module, env, val, attr, expected in OVERRIDES:
            with self.subTest(module=module.__name__, env=env):
                os.environ[env] = val
                try:
                    importlib.reload(module)
                    self.assertAlmostEqual(getattr(module, attr), expected, places=6)
                finally:
                    os.environ.pop(env, None)
                    importlib.reload(module)   # readuce modulul la default pt restul


class TestModuleSpecifics(unittest.TestCase):
    """Verificari specifice per modul (nu se preteaza la tabel)."""

    def test_rtrade_import_does_not_start_live_bot(self):
        # rtrade rula INAINTE bot.run() (bucla LIVE) neconditionat la import;
        # mutat sub __main__ -> importul trebuie sa fie sigur.
        self.assertFalse(hasattr(rt, "bot"),
                         "import rtrade nu trebuie sa instantieze/porneasca botul")

    def test_assetguardian_import_does_not_run_forever(self):
        self.assertTrue(hasattr(ag, "run_forever"))   # exista, dar guard-ul __main__ nu l-a rulat

    def test_monitortrades_dead_symbol_params_removed(self):
        # SYMBOL_PARAMS (linii per-simbol din monitortrades.conf) era parsat dar
        # niciodata citit — sursa reala e instruments.conf. Eliminat.
        self.assertFalse(hasattr(mt, "SYMBOL_PARAMS"))

    def test_monitortrades_global_hardtp_fallbacks_loaded(self):
        self.assertTrue(mt.HARD_TP_ENABLED)
        self.assertEqual(mt.TP_REFERENCE, "last")

    def test_rtrade_buy_sell_asymmetry_preserved(self):
        self.assertNotEqual(rt.RTRADE_BUY_DECAY_PCT, rt.RTRADE_SELL_DECAY_PCT)
        self.assertNotEqual(rt.RTRADE_BUY_DESPERATE_HOURS_BASE, rt.RTRADE_SELL_DESPERATE_HOURS_BASE)
        self.assertNotEqual(rt.RTRADE_BUY_NORMAL_HOURS, rt.RTRADE_SELL_NORMAL_HOURS)


if __name__ == "__main__":
    unittest.main()

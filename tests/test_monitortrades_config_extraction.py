"""
Teste pentru extragerea in variabile de mediu a constantelor GLOBALE hardcodate
din monitortrades.py (23 iul, cerere user: "inventariaza toti parametrii
vizi/constante si scoate-i in fisiere de config" — un bot pe rand, dupa
tradeall.py urmeaza monitortrades.py).

Nota: monitortrades.py avea deja DOUA nivele de config (monitortrades.conf +
instruments.conf, namespace mt.*) pt parametrii PER-INSTRUMENT (gain/lost/
maxage/hardtp/buy_budget etc.) — acelea NU sunt atinse aici. Acest fisier
acopera doar constantele GLOBALE care nu aveau inca niciun mecanism de
override (toleranta are_close, ferestrele de "trade recent", intervalul
buclei principale, ofsetul de pret BUY, ferestrele de safeback).

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

import monitortrades as mt

ENV_VARS = [
    "MT_ARE_CLOSE_TOLERANCE_PCT", "MT_RECENT_TRADE_BLOCK_HOURS",
    "MT_ALL_TRADES_BLOCK_HOURS", "MT_MAIN_LOOP_SLEEP_SEC",
    "MT_BUY_PRICE_OFFSET", "MT_SELL_SAFEBACK_HOURS", "MT_BUY_SAFEBACK_HOURS",
]


class TestDefaultsMatchOldHardcodedValues(unittest.TestCase):
    """Valorile implicite (din monitortrades_config.env, mereu incarcat la
    import) trebuie sa fie IDENTICE cu vechile constante hardcodate."""

    def test_are_close_tolerance(self):
        self.assertEqual(mt.MT_ARE_CLOSE_TOLERANCE_PCT, 1.0)

    def test_recent_trade_block_sec(self):
        self.assertEqual(mt.MT_RECENT_TRADE_BLOCK_SEC, 3 * 60 * 60)

    def test_all_trades_block_sec(self):
        self.assertEqual(mt.MT_ALL_TRADES_BLOCK_SEC, 1 * 60 * 60)

    def test_main_loop_sleep_sec(self):
        self.assertEqual(mt.MT_MAIN_LOOP_SLEEP_SEC, 60 * 0.8)

    def test_buy_price_offset(self):
        self.assertEqual(mt.MT_BUY_PRICE_OFFSET, 0.5)

    def test_safeback_hours(self):
        self.assertEqual(mt.MT_SELL_SAFEBACK_HOURS, 2)
        self.assertEqual(mt.MT_BUY_SAFEBACK_HOURS, 48)


class TestEnvOverrideActuallyWorks(unittest.TestCase):
    """Setarea variabilei de mediu ÎNAINTE de (re)import chiar schimba valoarea."""

    def tearDown(self):
        for v in ENV_VARS:
            os.environ.pop(v, None)
        importlib.reload(mt)

    def test_override_are_close_tolerance(self):
        os.environ["MT_ARE_CLOSE_TOLERANCE_PCT"] = "2.5"
        importlib.reload(mt)
        self.assertEqual(mt.MT_ARE_CLOSE_TOLERANCE_PCT, 2.5)

    def test_override_recent_trade_block_hours(self):
        os.environ["MT_RECENT_TRADE_BLOCK_HOURS"] = "5"
        importlib.reload(mt)
        self.assertEqual(mt.MT_RECENT_TRADE_BLOCK_SEC, 5 * 3600)

    def test_override_main_loop_sleep_sec(self):
        os.environ["MT_MAIN_LOOP_SLEEP_SEC"] = "30"
        importlib.reload(mt)
        self.assertEqual(mt.MT_MAIN_LOOP_SLEEP_SEC, 30)

    def test_override_buy_price_offset(self):
        os.environ["MT_BUY_PRICE_OFFSET"] = "1.0"
        importlib.reload(mt)
        self.assertEqual(mt.MT_BUY_PRICE_OFFSET, 1.0)

    def test_override_safeback_hours(self):
        os.environ["MT_SELL_SAFEBACK_HOURS"] = "4"
        os.environ["MT_BUY_SAFEBACK_HOURS"] = "24"
        importlib.reload(mt)
        self.assertEqual(mt.MT_SELL_SAFEBACK_HOURS, 4)
        self.assertEqual(mt.MT_BUY_SAFEBACK_HOURS, 24)


if __name__ == "__main__":
    unittest.main()

"""
Teste pentru parsarea instruments.conf (23 iul).

Motiv: gasit un bug real in timpul sesiunii — configparser.ConfigParser() NU
taie comentariile INLINE (pe aceeasi linie cu valoarea) by default, doar cele
pe linie proprie. KRAKEN_HYPE avea "mt.buy_budget = 200  # comentariu" -> se
parsa literal ca string-ul "200  # comentariu", float() esua in
Instrument.param(cast=float), cadea tacut pe default (None) -> "protectia"
de buy_budget/max_budget nu functiona niciodata, desi parea configurata.

Acoperire:
  - Fiecare parametru NUMERIC mt.* (gain/lost/maxage_days/hardtp/
    hardtp_fraction/hardtp_cooldown_h/buy_budget/max_budget) al oricarui
    ENABLED instrument, IF present in the file, must parse to a valid
    float (not None because of a stray inline comment).
  - Regresie directa: BINANCE_BTC/BINANCE_TAO (buy_budget=250, max_budget=3500,
    adaugate azi) si KRAKEN_HYPE (buy_budget=200, max_budget=700, reparate azi).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import instruments_config as ic

NUMERIC_MT_KEYS = ["gain", "lost", "maxage_days", "hardtp", "hardtp_fraction",
                   "hardtp_cooldown_h", "buy_budget", "max_budget"]


class TestNoInlineCommentCorruption(unittest.TestCase):
    """Pentru fiecare instrument mt.*, orice cheie NUMERICA prezenta in
    instruments.conf must parse to a float — if it is missing entirely that is
    fine (param() returns the code default), but if the file DOES have the
    line, it must not be corrupted by a stray inline comment."""

    def test_all_enabled_mt_instruments_parse_numeric_params(self):
        instruments = ic.load_instruments()
        raw_sections = _raw_param_presence()
        for name, inst in instruments.items():
            if not inst.enabled:
                continue
            for key in NUMERIC_MT_KEYS:
                present_in_file = f"mt.{key}" in raw_sections.get(name, set())
                if not present_in_file:
                    continue
                value = inst.param("mt", key, None, float)
                self.assertIsNotNone(
                    value, f"[{name}] mt.{key} e prezent in instruments.conf dar "
                            f"param(cast=float) a intors None — probabil comentariu inline scapat")

    def test_budget_regressions(self):
        instruments = ic.load_instruments()
        cases = {
            "BINANCE_BTC": (250.0, 3500.0),
            "BINANCE_TAO": (250.0, 3500.0),
            "KRAKEN_HYPE": (200.0, 5000.0),  # 30 iul: 700 -> 5000 (cerere user)
        }
        for name, (buy_budget, max_budget) in cases.items():
            with self.subTest(instrument=name):
                inst = instruments[name]
                self.assertEqual(inst.param("mt", "buy_budget", None, float), buy_budget)
                self.assertEqual(inst.param("mt", "max_budget", None, float), max_budget)


def _raw_param_presence():
    """Read instruments.conf DIRECTLY (not through Instrument) so we know, per
    section, which mt.* keys are DECLARED in the file (whether or not they parse
    correctly) — used only so we do not test keys that are intentionally absent."""
    import configparser
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instruments.conf")
    cp = configparser.ConfigParser()
    cp.read(path)
    out = {}
    for section in cp.sections():
        out[section] = {k for k in cp[section] if k.startswith("mt.")}
    return out


if __name__ == "__main__":
    unittest.main()

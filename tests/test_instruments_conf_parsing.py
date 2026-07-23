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
    instrument ENABLED, DACA e prezent in fisier, trebuie sa parseze la un
    float valid (nu None din cauza unui comentariu inline scapat).
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
    instruments.conf trebuie sa parseze la un float — daca lipseste complet,
    e OK (param() intoarce default-ul din cod), dar daca fisierul CHIAR are
    linia, ea nu trebuie sa fie corupta de un comentariu inline scapat."""

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

    def test_binance_btc_regression(self):
        instruments = ic.load_instruments()
        inst = instruments["BINANCE_BTC"]
        self.assertEqual(inst.param("mt", "buy_budget", None, float), 250.0)
        self.assertEqual(inst.param("mt", "max_budget", None, float), 3500.0)

    def test_binance_tao_regression(self):
        instruments = ic.load_instruments()
        inst = instruments["BINANCE_TAO"]
        self.assertEqual(inst.param("mt", "buy_budget", None, float), 250.0)
        self.assertEqual(inst.param("mt", "max_budget", None, float), 3500.0)

    def test_kraken_hype_regression_inline_comment_fix(self):
        instruments = ic.load_instruments()
        inst = instruments["KRAKEN_HYPE"]
        self.assertEqual(inst.param("mt", "buy_budget", None, float), 200.0)
        self.assertEqual(inst.param("mt", "max_budget", None, float), 700.0)


def _raw_param_presence():
    """Citeste instruments.conf DIRECT (nu prin Instrument) ca sa stim, per
    sectiune, ce chei mt.* sunt DECLARATE in fisier (indiferent daca parseaza
    corect sau nu) — folosit doar ca sa nu testam chei care lipsesc intentionat."""
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

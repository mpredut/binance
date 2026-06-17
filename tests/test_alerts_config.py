#!/usr/bin/env python3
"""Teste pt parserul de config al monitorului de alerte (market_alerts.conf)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alerts_config import load_config, resolve  # noqa: E402

SAMPLE = """
# comentariu
watch = BTC, TAO, HYPE
sources = coinmarketcap, coingecko
default  = 4.1 / 7.5
new_coin = 12 / 25
BTC = 6 / 10      # inline comment
ETH = 5 / 9
cooldown_minutes = 45
max_new_coins = 8
linie_malformata fara egal
gunoi = / /
"""


def _tmp(content):
    f = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
    f.write(content); f.close()
    return f.name


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(_tmp(SAMPLE))

    def test_watch_si_sources(self):
        self.assertEqual(self.cfg["watch"], ["BTC", "TAO", "HYPE"])
        self.assertEqual(self.cfg["sources"], ["coinmarketcap", "coingecko"])

    def test_praguri_bucket(self):
        ac = self.cfg["alert_config"]
        self.assertEqual(ac["default"], {"up_percent": 4.1, "down_percent": 7.5})
        self.assertEqual(ac["dynamic"], {"up_percent": 12.0, "down_percent": 25.0})

    def test_praguri_per_moneda(self):
        per = self.cfg["alert_config"]["per_coin"]
        self.assertEqual(per["BTC"], {"up_percent": 6.0, "down_percent": 10.0})
        self.assertEqual(per["ETH"], {"up_percent": 5.0, "down_percent": 9.0})

    def test_setari(self):
        self.assertEqual(self.cfg["alert_config"]["cooldown_minutes"], 45)
        self.assertEqual(self.cfg["max_new_coins"], 8)
        self.assertEqual(self.cfg["max_monitored"], 20)  # neschimbat -> default

    def test_malformat_ignorat(self):
        # liniile gresite nu strica nimic, raman default-urile
        self.assertEqual(self.cfg["alert_config"]["lookback_hours"], 24)


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.ac = load_config(_tmp(SAMPLE))["alert_config"]

    def test_per_moneda_castiga(self):
        self.assertEqual(resolve(self.ac, "BTC", is_dynamic=False)["up_percent"], 6.0)

    def test_moneda_normala_default(self):
        self.assertEqual(resolve(self.ac, "SOL", is_dynamic=False)["up_percent"], 4.1)

    def test_moneda_noua_dynamic(self):
        self.assertEqual(resolve(self.ac, "FOONEW", is_dynamic=True)["down_percent"], 25.0)

    def test_per_moneda_bate_si_dynamic(self):
        # daca o moneda are prag propriu, conteaza chiar daca e marcata noua
        self.assertEqual(resolve(self.ac, "BTC", is_dynamic=True)["up_percent"], 6.0)


class TestLipsa(unittest.TestCase):
    def test_fara_fisier_da_defaults(self):
        cfg = load_config("/nu/exista/deloc.conf")
        self.assertEqual(cfg["watch"], ["BTC", "TAO", "HYPE"])
        self.assertEqual(cfg["alert_config"]["default"]["up_percent"], 4.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Faza 5 provider-unify — GARDA UNICA: TOTI providerii de venue satisfac contractul
StrategyExecutor. Daca cineva adauga o metoda in contract dar uita un provider (sau
schimba o semnatura), acest test pica -> nu ajunge base v2 sa crape pe acel venue.

Parametrizat peste kraken / hyperliquid / binance / Trading212. Instantiere fara retea (clientii sunt
lazy); verificam DOAR ca interfata exista si e apelabila, nu comportamentul (acela e in
testele per-provider test_*_provider_executor.py)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.strategy_executor import (  # noqa: E402
    OrderReconciliationCapabilities,
    StrategyExecutor,
)
from providers.kraken_provider import KrakenProvider  # noqa: E402
from providers.hyperliquid_provider import HyperliquidProvider  # noqa: E402
from providers.market_api import BinanceProvider  # noqa: E402
from providers.t212_provider import T212Provider  # noqa: E402

# Metodele cerute de strategies/spot_dca.py (motorul base v2), agnostice de venue.
CONTRACT_METHODS = (
    "get_current_price", "submit_order", "order_status", "cancel_order",
    "pair_precision", "free_balance", "ohlc_closes",
    "reconciliation_capabilities",
)

EXPECTED_RECONCILIATION = {
    "kraken": OrderReconciliationCapabilities(True, True, True, True),
    "hyperliquid": OrderReconciliationCapabilities(True, True, True, True),
    "binance": OrderReconciliationCapabilities(True, True, True, True),
    "trading212": OrderReconciliationCapabilities(False, True, True, False),
}


def _providers():
    return [
        ("kraken", KrakenProvider()),
        ("hyperliquid", HyperliquidProvider(token="HYPE")),
        ("binance", BinanceProvider()),
        ("trading212", T212Provider()),
    ]


class ProviderContractConformanceTest(unittest.TestCase):
    def test_toti_providerii_satisfac_StrategyExecutor(self):
        for name, prov in _providers():
            with self.subTest(provider=name):
                self.assertIsInstance(prov, StrategyExecutor,
                                      f"{name} NU satisface contractul StrategyExecutor")

    def test_toate_metodele_contractului_exista_si_sunt_apelabile(self):
        for name, prov in _providers():
            for meth in CONTRACT_METHODS:
                with self.subTest(provider=name, method=meth):
                    self.assertTrue(callable(getattr(prov, meth, None)),
                                    f"{name}.{meth} lipseste sau nu e apelabil")

    def test_capabilitatile_de_reconciliere_sunt_declarate_explicit(self):
        for name, provider in _providers():
            with self.subTest(provider=name):
                self.assertEqual(
                    provider.reconciliation_capabilities(),
                    EXPECTED_RECONCILIATION[name],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

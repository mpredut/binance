"""Faza 5 provider-unify — GARDA UNICA: TOTI providerii de venue satisfac contractul
StrategyExecutor. Daca cineva adauga o metoda in contract dar uita un provider (sau
schimba o semnatura), acest test pica -> nu ajunge base v2 sa crape pe acel venue.

Parametrizat peste kraken / hyperliquid / binance. Instantiere fara retea (clientii sunt
lazy); verificam DOAR ca interfata exista si e apelabila, nu comportamentul (acela e in
testele per-provider test_*_provider_executor.py)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.strategy_executor import StrategyExecutor  # noqa: E402
from providers.kraken_provider import KrakenProvider  # noqa: E402
from providers.hyperliquid_provider import HyperliquidProvider  # noqa: E402
from providers.market_api import BinanceProvider  # noqa: E402

# Metodele cerute de kraken/strategy.py (motorul base v2), agnostice de venue.
CONTRACT_METHODS = (
    "get_current_price", "submit_order", "order_status", "cancel_order",
    "pair_precision", "free_balance", "ohlc_closes",
)


def _providers():
    return [
        ("kraken", KrakenProvider()),
        ("hyperliquid", HyperliquidProvider(token="HYPE")),
        ("binance", BinanceProvider()),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

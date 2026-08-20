"""Faza 1 provider-unify — kraken_provider satisface contractul StrategyExecutor
prin delegare la kraken_client. Client FAKE injectat (fara retea/chei)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.kraken_provider import KrakenProvider  # noqa: E402
from providers.strategy_executor import (  # noqa: E402
    StrategyExecutor, OrderStatus, PairPrecision, ProviderError)


class FakeClient:
    def __init__(self):
        self.calls = []

    def add_order(self, pair, side, volume, price=None, ordertype="limit", validate=False):
        self.calls.append(("add_order", pair, side, volume, price, ordertype, validate))
        return {"txid": ["OABC-123"], "descr": {}}

    def query_orders(self, txids):
        return {txids: {"status": "closed", "vol_exec": "2.5", "cost": "150.0", "fee": "0.39"}}

    def cancel_order(self, txid):
        self.calls.append(("cancel_order", txid))
        return {"count": 1}

    def pair_info(self, pair):
        return {"pair_decimals": 2, "lot_decimals": 8, "ordermin": "0.1"}

    def ohlc_closes(self, pair, interval):
        return [10.0, 11.0, 12.0]


def _provider(fake):
    p = KrakenProvider()
    p._cli = fake                    # scurtcircuiteaza _client() (fara chei/retea)
    return p


class KrakenExecutorContractTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        self.p = _provider(self.fake)

    def test_satisface_protocolul(self):
        self.assertIsInstance(self.p, StrategyExecutor)

    def test_submit_order_limit_intoarce_order_id(self):
        oid = self.p.submit_order("HYPEUSD", "buy", 2.5, price=60.0)
        self.assertEqual(oid, "OABC-123")
        # a delegat corect: limit, pret pasat, validate=False
        self.assertEqual(self.fake.calls[-1],
                         ("add_order", "HYPEUSD", "buy", 2.5, 60.0, "limit", False))

    def test_submit_order_market_fara_pret(self):
        self.p.submit_order("HYPEUSD", "sell", 1.0, price=59.0, market=True)
        c = self.fake.calls[-1]
        self.assertEqual(c[5], "market")     # ordertype
        self.assertIsNone(c[4])              # pretul e None la market

    def test_submit_order_fara_txid_ridica(self):
        self.fake.add_order = lambda *a, **k: {"descr": {}}   # raspuns fara txid
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)

    def test_submit_order_eroare_venue_devine_ProviderError(self):
        def boom(*a, **k):
            raise RuntimeError("Kraken: Insufficient funds")
        self.fake.add_order = boom
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)

    def test_order_status_mapare(self):
        st = self.p.order_status("HYPEUSD", "OABC-123")
        self.assertIsInstance(st, OrderStatus)
        self.assertEqual(st.status, "closed")
        self.assertEqual(st.filled_qty, 2.5)
        self.assertEqual(st.cost, 150.0)
        self.assertEqual(st.fee, 0.39)

    def test_order_status_lipsa_ridica(self):
        self.fake.query_orders = lambda txids: {}      # ordinul nu apare
        with self.assertRaises(ProviderError):
            self.p.order_status("HYPEUSD", "NOPE")

    def test_cancel_deleaga(self):
        self.p.cancel_order("HYPEUSD", "OABC-123")
        self.assertEqual(self.fake.calls[-1], ("cancel_order", "OABC-123"))

    def test_cancel_idempotent_pe_ordin_necunoscut(self):
        def boom(txid):
            raise RuntimeError("EOrder:Unknown order")
        self.fake.cancel_order = boom
        self.p.cancel_order("HYPEUSD", "GONE")         # NU trebuie sa ridice (idempotent)

    def test_cancel_neconfirmat_ridica(self):
        self.fake.cancel_order = lambda txid: {"count": 0}
        with self.assertRaises(ProviderError):
            self.p.cancel_order("HYPEUSD", "OABC-123")

    def test_pair_precision_mapare(self):
        pp = self.p.pair_precision("HYPEUSD")
        self.assertEqual(pp, PairPrecision(price_decimals=2, volume_decimals=8, order_min=0.1))

    def test_pair_precision_nelistat_intoarce_none(self):
        self.fake.pair_info = lambda pair: None
        self.assertIsNone(self.p.pair_precision("NEWX"))

    def test_ohlc_closes_deleaga(self):
        self.assertEqual(self.p.ohlc_closes("HYPEUSD", 240), [10.0, 11.0, 12.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

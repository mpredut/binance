"""Faza 3 provider-unify — hyperliquid_provider satisface contractul StrategyExecutor
(cablare API HL). Client HL FAKE injectat (fara SDK/retea/chei)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.hyperliquid_provider import HyperliquidProvider  # noqa: E402
from providers.strategy_executor import (  # noqa: E402
    StrategyExecutor, OrderStatus, PairPrecision, ProviderError)


class FakeInfo:
    def __init__(self, fills, status="open"):
        self._fills = fills
        self._status = status

    def user_fills(self, addr):
        return self._fills

    def query_order_by_oid(self, addr, oid):
        if self._status == "unknownOid":
            return {"status": "unknownOid"}
        filled = sum(float(item.get("sz") or 0.0) for item in self._fills
                     if int(item.get("oid", -1)) == oid)
        original = max(1.0, filled)
        remaining = 0.0 if self._status == "filled" else original - filled
        return {
            "status": "order",
            "order": {
                "status": self._status,
                "order": {"origSz": str(original), "sz": str(remaining)},
            },
        }


class FakeRead:
    def __init__(self, fills=None, opens=None, status="open"):
        self.info = FakeInfo(fills or [], status=status)
        self._opens = opens or []

    def sz_decimals(self, coin):
        return 2

    def open_orders(self, pair):
        return self._opens

    def spot_mid(self, pair):
        return 60.0

    def candles(self, pair, iv, lh):
        return [{"c": "10"}, {"c": "11"}, {"c": "12"}]


class FakeSigner:
    def __init__(self, order_res=(True, 12345, "resting"), cancel_res=True):
        self._o, self._c, self.calls = order_res, cancel_res, []

    def sz_decimals(self, coin):
        return 2

    def spot_order(self, pair, is_buy, sz, px, sz_decimals=2, cloid=None):
        self.calls.append(("spot_order", pair, is_buy, sz, px, cloid))
        return self._o

    def cancel(self, pair, oid):
        self.calls.append(("cancel", pair, oid))
        return self._c


def _provider(read, signer):
    p = HyperliquidProvider(token="HYPE")
    p._client = read
    p._client_tried = True
    p._spot_pair = "@107"
    p._signer = lambda: signer
    return p


class HLExecutorContractTest(unittest.TestCase):
    def setUp(self):
        os.environ["HL_ACCOUNT_ADDRESS"] = "0xABC"
        os.environ.pop("HL_LIVE_ORDERS", None)
        self.signer = FakeSigner()
        self.p = _provider(FakeRead(), self.signer)

    def tearDown(self):
        os.environ.pop("HL_LIVE_ORDERS", None)

    def test_satisface_protocolul(self):
        self.assertIsInstance(self.p, StrategyExecutor)

    def test_pair_precision(self):
        pp = self.p.pair_precision("HYPE")
        # spot: price_decimals = 8 - szDecimals(2) = 6
        self.assertEqual(pp, PairPrecision(price_decimals=6, volume_decimals=2,
                                           order_min=0.0, base_asset="HYPE"))

    def test_ohlc_closes_exclude_bara_in_formare(self):
        self.assertEqual(self.p.ohlc_closes("HYPE", 240), [10.0, 11.0])

    def test_submit_order_gated_pe_HL_LIVE_ORDERS(self):
        # implicit HL_LIVE_ORDERS lipseste -> refuz (siguranta co-mingling DN)
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPE", "buy", 1.0, price=60.0)

    def test_submit_order_live_intoarce_order_id(self):
        os.environ["HL_LIVE_ORDERS"] = "true"
        oid = self.p.submit_order("HYPE", "buy", 1.0, price=60.0)
        self.assertEqual(oid, "12345")
        self.assertEqual(self.signer.calls[-1][:3], ("spot_order", "@107", True))

    def test_submit_order_propaga_cloid(self):
        os.environ["HL_LIVE_ORDERS"] = "true"
        cloid = "0x0123456789abcdef0123456789abcdef"
        self.p.submit_order(
            "HYPE", "buy", 1.0, price=60.0, client_order_id=cloid,
        )
        self.assertEqual(self.signer.calls[-1][-1], cloid)

    def test_submit_order_market_incruciseaza_pretul(self):
        os.environ["HL_LIVE_ORDERS"] = "true"
        self.p.submit_order("HYPE", "sell", 1.0, price=None, market=True)
        px = self.signer.calls[-1][4]
        self.assertAlmostEqual(px, 60.0 * 0.95)     # sell market -> sub mid, fill imediat

    def test_submit_order_respins_ridica(self):
        os.environ["HL_LIVE_ORDERS"] = "true"
        self.p._signer = lambda: FakeSigner(order_res=(False, None, "Insufficient"))
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPE", "buy", 1.0, price=60.0)

    def test_order_status_open(self):
        self.p._client = FakeRead(status="open")
        st = self.p.order_status("HYPE", "999")
        self.assertEqual(st.status, "open")

    def test_order_status_open_include_fill_partial(self):
        self.p._client = FakeRead(
            fills=[{"oid": 999, "sz": "0.4", "px": "60", "fee": "0.02"}],
            status="open",
        )
        st = self.p.order_status("HYPE", "999")
        self.assertEqual(st.status, "open")
        self.assertAlmostEqual(st.filled_qty, 0.4)
        self.assertAlmostEqual(st.cost, 24.0)
        self.assertAlmostEqual(st.fee, 0.02)

    def test_order_status_closed_agrega_fills(self):
        self.p._client = FakeRead(
            fills=[
                {"oid": 5, "sz": "1.5", "px": "60", "fee": "0.1"},
                {"oid": 5, "sz": "0.5", "px": "62", "fee": "0.05"},
            ],
            status="filled",
        )
        st = self.p.order_status("HYPE", "5")
        self.assertEqual(st.status, "closed")
        self.assertAlmostEqual(st.filled_qty, 2.0)
        self.assertAlmostEqual(st.cost, 1.5 * 60 + 0.5 * 62)
        self.assertAlmostEqual(st.fee, 0.15)

    def test_order_status_canceled_din_endpoint_dedicat(self):
        self.p._client = FakeRead(status="canceled")
        st = self.p.order_status("HYPE", "77")
        self.assertEqual(st.status, "canceled")

    def test_order_status_necunoscut_ramane_nedeterminat(self):
        self.p._client = FakeRead(status="unknownOid")
        with self.assertRaises(ProviderError):
            self.p.order_status("HYPE", "77")

    def test_order_terminal_asteapta_fills_complete(self):
        read = FakeRead(status="filled")
        read.info.query_order_by_oid = lambda addr, oid: {
            "status": "order",
            "order": {
                "status": "filled",
                "order": {"origSz": "1", "sz": "0"},
            },
        }
        self.p._client = read
        with self.assertRaisesRegex(ProviderError, "fills incomplete"):
            self.p.order_status("HYPE", "77")

    def test_cancel_deleaga_la_signer(self):
        self.p.cancel_order("HYPE", "5")
        self.assertIn(("cancel", "@107", 5), self.signer.calls)

    def test_cancel_neconfirmat_ridica(self):
        self.p._signer = lambda: FakeSigner(cancel_res=False)
        with self.assertRaises(ProviderError):
            self.p.cancel_order("HYPE", "5")


if __name__ == "__main__":
    unittest.main(verbosity=2)

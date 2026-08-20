"""Faza 2 provider-unify — caile LIVE ale strategiei (dry_run=False) trec prin
contractul StrategyExecutor: submit_order / order_status / cancel_order.

GOLDEN-ul acopera deciziile (replay, dry_run); ASTA acopera exact partea pe care
golden-ul NU o atinge: rewire-ul de la KrakenClient la contract in _place/reconcile/cancel.
Provider FAKE (fara retea)."""
import os
import sys
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from strategies import spot_dca as strat  # noqa: E402
from providers.strategy_executor import OrderStatus, PairPrecision, ProviderError  # noqa: E402


class FakeExecutor:
    """Implementeaza contractul StrategyExecutor, inregistreaza apelurile."""
    def __init__(self):
        self.calls = []
        self.next_status = OrderStatus("open", 0.0, 0.0, 0.0)
        self._seq = 0

    def get_current_price(self, symbol):
        return 60.0

    def submit_order(self, symbol, side, qty, price=None, *, market=False, kind=None):
        self._seq += 1
        self.calls.append(("submit_order", symbol, side, qty, price, market, kind))
        return f"OID-{self._seq}"

    def order_status(self, symbol, order_id):
        self.calls.append(("order_status", symbol, order_id))
        return self.next_status

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))

    def pair_precision(self, symbol):
        return PairPrecision(price_decimals=2, volume_decimals=8, order_min=0.0, base_asset="HYPE")

    def free_balance(self, asset):
        return 0.0

    def ohlc_closes(self, symbol, interval_min):
        return []


def _strategy(fake, **over):
    p = dict(currency="USD", entry_amount=650.0, entry_discount_pct=0.8, dca_amount=325.0,
             dca_drop_pct=1.25, check_minutes=2.0, takeprofit_pct=5.0, max_budget=3900.0,
             max_dca_buys=10, enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=12.5,
             adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=2.2, reentry_tolerance_pct=0.05,
             reentry_adaptive=False, reentry_sl_bounce_pct=1.5, tp_tranches=[],
             tp_trend_hold=True, tp_trail_pct=3.0)
    p.update(over)
    s = strat.Strategy(fake, "HYPEUSD", strat.StratParams(**p),
                       dry_run=False, initial_state=strat._new_state())
    s._save = lambda *a, **k: None
    return s


class ProviderLivePathTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeExecutor()
        self._orig_notify = strat.notify
        strat.notify = lambda *a, **k: None      # fara push in teste
        self.s = _strategy(self.fake)

    def tearDown(self):
        strat.notify = self._orig_notify

    def test_init_citeste_precizia_prin_contract(self):
        self.assertEqual((self.s.price_dec, self.s.vol_dec), (2, 8))

    def test_place_cheama_submit_order_si_stocheaza_order_id(self):
        self.s._save = MagicMock()
        self.s._place("buy", 1.0, 60.0, kind="ENTRY", amount=650.0)
        sub = [c for c in self.fake.calls if c[0] == "submit_order"]
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0][1:4], ("HYPEUSD", "buy", 1.0))
        self.assertEqual(self.s.s["orders"][-1]["txid"], "OID-1")
        self.s._save.assert_called_once()

    def test_place_market_propaga_flagul(self):
        self.s.s["qty"] = 5.0
        self.s._place("sell", 5.0, 59.0, kind="STOP", market=True)
        sub = [c for c in self.fake.calls if c[0] == "submit_order"][-1]
        self.assertTrue(sub[5])                  # market=True propagat

    def test_audited_market_submit_receives_observational_reference_price(self):
        class IntentExecutor(FakeExecutor):
            def submit_order_with_intent(
                self, intent_id, symbol, side, qty, price=None, *, market=False,
                kind=None, reference_price=None,
            ):
                self._seq += 1
                self.calls.append((
                    "submit_with_intent", symbol, side, qty, price, market, kind,
                    reference_price,
                ))
                return f"OID-{self._seq}"

        executor = IntentExecutor()
        strategy = _strategy(executor)
        strategy._place("sell", 5.0, 59.0, kind="STOP", market=True)

        call = next(item for item in executor.calls if item[0] == "submit_with_intent")
        self.assertIsNone(call[4])
        self.assertTrue(call[5])
        self.assertEqual(call[7], 59.0)

    def test_volatility_scaled_dca_amount_and_fail_safe(self):
        fixed = _strategy(self.fake, dca_vol_scale_k=0.0)
        self.assertEqual(fixed._effective_dca_amount(), 325.0)

        aggressive = _strategy(
            self.fake, dca_vol_scale_k=-1.0, dca_vol_ref=2.0,
        )
        aggressive._dca_vol_1h = lambda: 4.0
        self.assertEqual(aggressive._effective_dca_amount(), 650.0)
        aggressive._dca_vol_1h = lambda: 20.0
        self.assertEqual(aggressive._effective_dca_amount(), 975.0)

        defensive = _strategy(
            self.fake, dca_vol_scale_k=1.0, dca_vol_ref=2.0,
        )
        defensive._dca_vol_1h = lambda: 4.0
        self.assertEqual(defensive._effective_dca_amount(), 162.5)

        for bad_reference in (-1.0, float("nan")):
            invalid = _strategy(
                self.fake, dca_vol_scale_k=-1.0,
                dca_vol_ref=bad_reference,
            )
            invalid._dca_vol_1h = lambda: 4.0
            self.assertEqual(invalid._effective_dca_amount(), 325.0)

        aggressive._dca_vol_1h = lambda: None
        self.assertEqual(aggressive._effective_dca_amount(), 325.0)

    def test_dca_volatility_uses_closed_ohlc_in_live_mode(self):
        strategy = _strategy(
            self.fake, dca_vol_scale_k=-1.0, dca_vol_interval=240,
        )
        closes = [100.0 + (index % 3) for index in range(20)]
        strategy.client.ohlc_closes = MagicMock(return_value=closes)

        self.assertIsNotNone(strategy._dca_vol_1h())
        strategy.client.ohlc_closes.assert_called_once_with("HYPEUSD", 240)

    def test_place_ProviderError_nu_stocheaza_ordinul(self):
        def boom(*a, **k):
            raise ProviderError("Insufficient funds")
        self.fake.submit_order = boom
        self.s._place("buy", 1.0, 60.0, kind="ENTRY", amount=650.0)
        self.assertEqual(self.s.s["orders"], [])   # esec -> niciun ordin stocat

    def test_reconcile_umple_pe_closed_prin_order_status(self):
        self.s.s["orders"] = [{"txid": "OID-9", "side": "buy", "vol": 2.0, "price": 60.0,
                               "amount": 120.0, "kind": "ENTRY", "ts": 0}]
        self.fake.next_status = OrderStatus("closed", filled_qty=2.0, cost=120.0, fee=0.31)
        self.s.reconcile(60.0)
        self.assertTrue(any(c[0] == "order_status" for c in self.fake.calls))
        self.assertAlmostEqual(self.s.s["qty"], 2.0)     # fill aplicat
        self.assertEqual(self.s.s["orders"], [])         # ordinul consumat

    def test_cancel_open_cheama_cancel_order(self):
        self.s._save = MagicMock()
        self.s.s["orders"] = [{"txid": "OID-7", "side": "buy", "vol": 1.0, "price": 60.0,
                               "amount": 60.0, "kind": "ENTRY", "ts": 0}]
        self.s._cancel_open("buy")
        self.assertIn(("cancel_order", "HYPEUSD", "OID-7"), self.fake.calls)
        # Ordinul ramane urmarit pana la status terminal, pentru a nu pierde
        # un fill concurent cu anularea.
        self.assertTrue(self.s.s["orders"][0]["cancel_requested"])
        self.s._save.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

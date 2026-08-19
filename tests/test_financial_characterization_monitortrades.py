"""Characterization tests for monitortrades financial behaviour.

These tests intentionally record the CURRENT decision semantics before the order
pipeline is redesigned.  They use no network, real cache, credentials or exchange
client.  A failure during refactoring means that the financial behaviour changed
and the change must be reviewed explicitly.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import monitortrades as mt


NOW = 2_000_000_000.0
SYMBOL = "CHARUSD"


def _fill(side: str, price: float, qty: float, age_s: float = 4 * 3600) -> dict:
    return {
        "side": side,
        "price": float(price),
        "qty": float(qty),
        "timestamp": (NOW - age_s) * 1000,
    }


class FakeProvider:
    """Only the account-history contract used by get_position_stats."""

    def __init__(self, fills):
        self.fills = list(fills)

    def get_orders(self, symbol, side, since_s):
        return [dict(x) for x in self.fills if x["side"] == side]


class FakeInstrument(mt._Instrument):
    """Instrument-shaped test double that records final financial intents."""

    def __init__(self, *, price, free, fills, params=None, min_qty=0.0):
        # Deliberately do not call Instrument.__init__: no provider registry/API.
        self.symbol = SYMBOL
        self.name = "CHAR"
        self._price = float(price)
        self._free = float(free)
        self._fills = list(fills)
        self._params = dict(params or {})
        self._min_qty = float(min_qty)
        self._provider = FakeProvider(self._fills)
        self.placed = []

    def param(self, consumer, key, default=None, cast=None):
        value = self._params.get(f"{consumer}.{key}", default)
        if cast is None:
            return value
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    def orders(self, side, since_s):
        return [dict(x) for x in self._fills if x["side"] == side]

    def price(self):
        return self._price

    def free(self):
        return self._free

    def min_qty(self):
        return self._min_qty

    def place(self, side, price, qty, **kwargs):
        intent = {
            "side": side,
            "price": price,
            "qty": qty,
            "kwargs": dict(kwargs),
        }
        self.placed.append(intent)
        return {"orderId": f"fake-{len(self.placed)}"}


class MonitorTradesFinancialCharacterization(unittest.TestCase):
    def setUp(self):
        mt._hard_tp_last.clear()
        self._hard_tp_enabled = mt.HARD_TP_ENABLED
        mt.HARD_TP_ENABLED = True

    def tearDown(self):
        mt.HARD_TP_ENABLED = self._hard_tp_enabled
        mt._hard_tp_last.clear()

    def run_tick(self, inst, *, trend_up=False, gain=0.10, loss=0.05):
        with patch.object(mt, "is_trend_up", return_value=trend_up):
            mt.monitor_price_and_trade(
                inst,
                sbs=12 * 24 * 3600 + 60,
                maxage_trade_s=10 * 24 * 3600,
                gain_threshold=gain,
                lost_threshold=loss,
                now_fn=lambda: NOW,
            )

    def test_position_stats_are_gross_averages_and_net_quantity(self):
        provider = FakeProvider([
            _fill("BUY", 100, 2),
            _fill("BUY", 120, 1),
            _fill("SELL", 130, 0.5),
        ])
        got = mt.get_position_stats(SYMBOL, 10 * 24 * 3600, api=provider)
        self.assertEqual(got["buy_qty"], 3.0)
        self.assertEqual(got["sell_qty"], 0.5)
        self.assertEqual(got["net_qty"], 2.5)
        self.assertAlmostEqual(got["average_buy_price"], 320 / 3)
        self.assertEqual(got["average_sell_price"], 130.0)

    def test_hard_tp_sells_configured_fraction_and_stops_the_tick(self):
        inst = FakeInstrument(
            price=118,
            free=10,
            fills=[_fill("BUY", 100, 10)],
            params={"mt.hardtp": 17, "mt.hardtp_fraction": 0.5},
        )
        self.run_tick(inst, trend_up=True)  # hard TP is independent of uptrend
        self.assertEqual(len(inst.placed), 1)
        order = inst.placed[0]
        self.assertEqual(order["side"], "SELL")
        self.assertEqual(order["qty"], 5.0)
        self.assertTrue(order["kwargs"]["force"])
        self.assertFalse(order["kwargs"]["pair"])

    def test_normal_take_profit_sells_all_free_balance_when_trend_not_up(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(price=112, free=7.25, fills=[_fill("BUY", 100, 8)])
        self.run_tick(inst, trend_up=False, gain=0.10)
        self.assertEqual(len(inst.placed), 1)
        order = inst.placed[0]
        self.assertEqual((order["side"], order["price"], order["qty"]),
                         ("SELL", 112.0, 7.25))
        self.assertFalse(order["kwargs"]["force"])
        self.assertFalse(order["kwargs"]["pair"])

    def test_uptrend_blocks_normal_take_profit(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(price=112, free=7.25, fills=[_fill("BUY", 100, 8)])
        self.run_tick(inst, trend_up=True, gain=0.10)
        self.assertEqual(inst.placed, [])

    def test_loss_exit_sells_all_free_balance_with_pair_flag(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(price=93, free=4, fills=[_fill("BUY", 100, 4)])
        self.run_tick(inst, trend_up=False, loss=0.05)
        self.assertEqual(len(inst.placed), 1)
        order = inst.placed[0]
        self.assertEqual((order["side"], order["qty"]), ("SELL", 4.0))
        self.assertTrue(order["kwargs"]["pair"])

    def test_buyback_uses_budget_divided_by_market_price_and_adds_offset(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(
            price=90,
            free=2,
            fills=[_fill("SELL", 100, 2)],
            params={"mt.buy_budget": 225, "mt.max_budget": 1_000},
        )
        self.run_tick(inst, trend_up=True, gain=0.09)
        self.assertEqual(len(inst.placed), 1)
        order = inst.placed[0]
        self.assertEqual(order["side"], "BUY")
        self.assertEqual(order["qty"], 2.5)
        self.assertEqual(order["price"], 90 + mt.MT_BUY_PRICE_OFFSET)

    def test_buyback_is_blocked_when_existing_free_position_reaches_budget(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(
            price=90,
            free=10,
            fills=[_fill("SELL", 100, 2)],
            params={"mt.buy_budget": 225, "mt.max_budget": 900},
        )
        self.run_tick(inst, trend_up=True, gain=0.09)
        self.assertEqual(inst.placed, [])

    def test_average_reference_can_trigger_sell_when_last_buy_does_not(self):
        mt.HARD_TP_ENABLED = False
        inst = FakeInstrument(
            price=116,
            free=4,
            fills=[
                _fill("BUY", 120, 1, age_s=4 * 3600),  # latest buy
                _fill("BUY", 100, 3, age_s=5 * 3600),
            ],
            params={"mt.ref": "average"},
        )
        # Average is 105: +10.47% triggers. Last buy is 120 and would not trigger TP.
        self.run_tick(inst, trend_up=False, gain=0.10, loss=0.20)
        self.assertEqual(len(inst.placed), 1)
        self.assertEqual(inst.placed[0]["side"], "SELL")

    def test_fractional_hard_tp_below_minimum_falls_through_to_full_normal_sell(self):
        """Current behaviour: a skipped fractional hard-TP does not end the tick.

        The normal take-profit rule then sells the full free balance.  This is
        financially surprising, but is intentionally characterized rather than
        changed as part of the safety-baseline work.
        """
        inst = FakeInstrument(
            price=118,
            free=1,
            fills=[_fill("BUY", 100, 1)],
            params={"mt.hardtp": 17, "mt.hardtp_fraction": 0.5},
            min_qty=0.6,
        )
        self.run_tick(inst, trend_up=False)
        self.assertEqual(len(inst.placed), 1)
        self.assertEqual((inst.placed[0]["side"], inst.placed[0]["qty"]),
                         ("SELL", 1.0))
        self.assertFalse(inst.placed[0]["kwargs"]["force"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

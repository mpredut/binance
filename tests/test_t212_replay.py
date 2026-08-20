"""Trading212 replay rulează motorul live și păstrează contabilitatea parțială."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T212_DIR = os.path.join(ROOT, "212trading")
sys.path.insert(0, T212_DIR)
sys.path.insert(0, ROOT)

from providers.execution_audit import ExecutionAudit  # noqa: E402

_COLLIDING = ("strategy", "market_data", "notify", "ipo_notify", "replay")
_PRELOADED = {name: sys.modules.pop(name) for name in _COLLIDING if name in sys.modules}
try:
    spec = importlib.util.spec_from_file_location(
        "t212_replay_under_test", os.path.join(T212_DIR, "replay.py"),
    )
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    strategy = replay._strat
finally:
    for name in _COLLIDING:
        sys.modules.pop(name, None)
    sys.modules.update(_PRELOADED)


def _params(**overrides):
    config = {
        "STRAT_CURRENCY": "USD",
        "YAHOO_SYMBOL": "TEST",
        "STRAT_ENTRY": "100",
        "STRAT_DCA": "50",
        "STRAT_ENTRY_DISCOUNT_PCT": "0.2",
        "STRAT_DCA_DROP_PCT": "2",
        "STRAT_TAKEPROFIT_PCT": "3",
        "STRAT_MAX_DCA_BUYS": "3",
        "STRAT_MAX_BUDGET": "500",
        "STRAT_FX_FEE_PCT": "0.15",
        "STRAT_STOP_LOSS_PCT": "20",
    }
    config.update({key: str(value) for key, value in overrides.items()})
    return strategy.StratParams.from_env(config)


class T212ReplayTest(unittest.TestCase):
    def test_partial_fill_remains_open_for_later_bars(self):
        params = _params()
        bars = [(100, 101, 99, 100), (100, 101, 99, 100)]
        full = replay.run_replay(bars, params, bar_minutes=1440)
        partial = replay.run_replay(
            bars, params, bar_minutes=1440,
            execution=replay.ExecutionModel(partial_fill_ratio=0.5),
        )
        self.assertEqual(full["fills"], 1)
        self.assertEqual(partial["fills"], 1)
        self.assertAlmostEqual(partial["open_qty"], full["open_qty"] / 2)

    def test_historical_fx_changes_position_sizing_at_decision_time(self):
        params = _params(STRAT_CURRENCY="RON")
        bars = [(100, 101, 99, 100), (100, 101, 99, 100)]
        one_to_one = replay.run_replay(bars, params, bar_minutes=1440, fx_to_usd=1.0)
        historical = replay.run_replay(
            bars, params, bar_minutes=1440, fx_to_usd=[0.5, 0.5],
        )
        self.assertAlmostEqual(historical["open_qty"], one_to_one["open_qty"] / 2)
        self.assertEqual(historical["account_currency"], "RON")

    def test_worst_case_reports_ambiguous_buy_and_sell_paths(self):
        params = _params()
        bars = [
            (100, 101, 99, 100),   # decide ENTRY
            (100, 101, 99, 100),   # fill ENTRY, decide TP
            (97, 101, 96.9, 97),   # decide DCA; TP rămâne deschis
            (100, 104, 96, 100),   # atinge și DCA BUY, și TP SELL
        ]
        result = replay.run_replay(
            bars, params, bar_minutes=1440,
            execution=replay.ExecutionModel(intrabar_policy="worst_case"),
        )
        self.assertGreaterEqual(result["ambiguous_bars"], 1)
        self.assertIn(result["intrabar_policy_selected"], {"buy_first", "sell_first"})
        scenarios = result["intrabar_scenarios"]
        self.assertNotEqual(
            scenarios["buy_first"]["return_pct"],
            scenarios["sell_first"]["return_pct"],
        )

    def test_order_decided_at_close_fills_only_in_next_bar(self):
        params = _params()
        first = (100.0, 200.0, 1.0, 100.0)
        one = replay.run_replay([first], params, bar_minutes=1440)
        self.assertEqual(one["fills"], 0)
        self.assertEqual(one["open_qty"], 0.0)

        second = (100.0, 101.0, 99.0, 100.0)
        two = replay.run_replay([first, second], params, bar_minutes=1440)
        self.assertEqual(two["fills"], 1)
        self.assertGreater(two["open_qty"], 0.0)
        self.assertAlmostEqual(two["net_pnl"], two["total"])

    def test_market_stop_fills_at_next_open_and_slippage_is_adverse(self):
        params = _params(STRAT_STOP_LOSS_PCT="20")
        bars = [
            (100, 101, 99, 100),  # decide ENTRY
            (100, 101, 99, 100),  # fill ENTRY
            (70, 71, 69, 70),     # decide STOP MARKET la close
            (65, 66, 64, 65),     # fill STOP la open, chiar dupa gap
        ]
        base = replay.run_replay(bars, params, bar_minutes=1440)
        stressed = replay.run_replay(
            bars, params, bar_minutes=1440,
            execution=replay.ExecutionModel(market_slippage_bps=100),
        )
        self.assertEqual(base["open_qty"], 0.0)
        self.assertEqual(base["cycles"], 1)
        self.assertLess(stressed["total"], base["total"])

    def test_partial_sell_reduces_remaining_cost_basis(self):
        params = _params()
        engine = strategy.Strategy(
            MagicMock(), "TEST_US_EQ", params, dry_run=True,
            initial_state=strategy._new_state(), fx_to_usd=1.0,
        )
        engine.s.update({"qty": 2.0, "cost_usd": 200.0, "spent_cash": 200.0})
        strategy.notify = lambda **_kwargs: None
        engine._apply_fill(
            {"side": "SELL", "kind": "TP", "qty": 1.0, "limit": 110.0},
            1.0, 110.0,
        )
        self.assertEqual(engine.s["qty"], 1.0)
        self.assertAlmostEqual(engine.s["cost_usd"], 100.0)
        self.assertAlmostEqual(engine._avg_cost(), 100.0)

    def test_paper_stop_arms_same_rebuy_as_real_path(self):
        params = _params(STRAT_SL_REBUY_ENABLED="true", STRAT_SL_REBUY_BOUNCE_PCT="1.2")
        engine = strategy.Strategy(
            MagicMock(), "TEST_US_EQ", params, dry_run=True,
            initial_state=strategy._new_state(), fx_to_usd=1.0,
        )
        engine.s.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
            "sl_pending": True,
        })
        strategy.notify = lambda **_kwargs: None
        engine._apply_fill(
            {"side": "SELL", "kind": "SL", "qty": 1.0, "limit": 70.0},
            1.0, 70.0,
        )
        self.assertEqual(engine.s["last_sell_price"], 70.0)
        self.assertEqual(engine.s["sl_rebuy"], {"low": 70.0, "sell_price": 70.0})

    def test_trend_gate_refuses_wrong_cadence(self):
        params = _params(STRAT_DCA_TREND_GATE_PCT="0.1")
        with self.assertRaisesRegex(ValueError, "5 minute"):
            replay.run_replay([(100, 101, 99, 100)], params, bar_minutes=1440)


class T212StatePersistenceTest(unittest.TestCase):
    @staticmethod
    def _client():
        return MagicMock()

    def test_corrupt_state_fails_closed_live_but_may_reset_in_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not-json")
            with patch.object(strategy, "state_path_for", return_value=path):
                with self.assertRaisesRegex(RuntimeError, "stare T212 invalida"):
                    strategy.Strategy(
                        self._client(), "TEST_US_EQ", _params(), dry_run=False,
                        fx_to_usd=1.0,
                    )
                paper = strategy.Strategy(
                    self._client(), "TEST_US_EQ", _params(), dry_run=True,
                    fx_to_usd=1.0,
                )
            self.assertEqual(paper.s, strategy._new_state())

    def test_live_save_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            engine = strategy.Strategy(
                self._client(), "TEST_US_EQ", _params(), dry_run=False,
                initial_state=strategy._new_state(), fx_to_usd=1.0,
            )
            engine.state_file = path
            engine.s["qty"] = 1.25

            engine._save()

            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["qty"], 1.25)
            self.assertEqual(os.listdir(directory), ["state.json"])

    def test_failed_save_marks_state_dirty_and_live_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            for dry_run in (True, False):
                with self.subTest(dry_run=dry_run):
                    engine = strategy.Strategy(
                        self._client(), "TEST_US_EQ", _params(), dry_run=dry_run,
                        initial_state=strategy._new_state(), fx_to_usd=1.0,
                    )
                    engine.state_file = path
                    with patch(
                        "strategies.state_store.os.replace", side_effect=OSError("disk")
                    ):
                        if dry_run:
                            engine._save()
                        else:
                            with self.assertRaisesRegex(RuntimeError, "persistenta"):
                                engine._save()
                    self.assertTrue(engine._state_write_failed)


class _FillClient:
    def __init__(self):
        self.portfolio = [{"ticker": "TEST_US_EQ", "quantity": 0.5, "averagePrice": 100.0}]
        self.active = [{"id": "SELL-1", "ticker": "TEST_US_EQ"}]
        self.status = {
            "id": "SELL-1", "ticker": "TEST_US_EQ", "status": "PARTIALLY_FILLED",
            "filledQuantity": 0.5, "filledValue": 55.0,
        }
        self.cancel_calls = []

    def get_portfolio(self):
        return self.portfolio

    def list_active_orders(self):
        return self.active

    def get_order_status(self, order_id):
        return self.status

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        return True


class T212ExactFillReconciliationTest(unittest.TestCase):
    def _engine(self, client, audit_dir):
        state = strategy._new_state()
        state.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
            "entry_price": 100.0, "last_buy_price": 100.0,
            "orders": [{
                "id": "SELL-1", "side": "SELL", "qty": 1.0,
                "limit": 108.0, "kind": "TP", "level": None,
                "intent_id": "t212-test-sell", "ts": 0.0,
            }],
        })
        engine = strategy.Strategy(
            client, "TEST_US_EQ", _params(STRAT_FX_FEE_PCT="0"), dry_run=False,
            initial_state=state, fx_to_usd=1.0,
            execution_audit=ExecutionAudit(audit_dir),
        )
        engine._save = lambda: None
        return engine

    def test_partial_and_terminal_sell_use_cumulative_fill_prices_not_poll_price(self):
        client = _FillClient()
        with tempfile.TemporaryDirectory() as audit_dir:
            engine = self._engine(client, audit_dir)
            with patch.object(strategy, "notify"):
                engine._reconcile_real(150.0)  # poll-ul e deliberat departe de fill-ul 110

            self.assertAlmostEqual(engine.s["qty"], 0.5)
            self.assertAlmostEqual(engine.s["realized_pnl_usd"], 5.0)
            self.assertAlmostEqual(engine.s["last_sell_price"], 110.0)
            self.assertAlmostEqual(engine.s["orders"][0]["applied_fill_qty"], 0.5)

            client.portfolio = [{"ticker": "TEST_US_EQ", "quantity": 0.0, "averagePrice": 0.0}]
            client.active = []
            client.status = {
                "id": "SELL-1", "ticker": "TEST_US_EQ", "status": "FILLED",
                "filledQuantity": 1.0, "filledValue": 112.0,
            }
            with patch.object(strategy, "notify"):
                engine._reconcile_real(160.0)

            # A doua jumatate s-a executat la 114; total brut = 5 + 7, nu la 150/160.
            self.assertAlmostEqual(engine.s["realized_pnl_usd"], 12.0)
            self.assertAlmostEqual(engine.s["last_sell_price"], 114.0)
            self.assertEqual(engine.s["qty"], 0.0)

    def test_canceled_unfilled_ladder_order_is_not_marked_as_sold(self):
        client = _FillClient()
        client.portfolio = [{"ticker": "TEST_US_EQ", "quantity": 1.0, "averagePrice": 100.0}]
        client.active = []
        client.status = {
            "id": "SELL-1", "ticker": "TEST_US_EQ", "status": "CANCELLED",
            "filledQuantity": 0.0, "filledValue": 0.0,
        }
        with tempfile.TemporaryDirectory() as audit_dir:
            engine = self._engine(client, audit_dir)
            engine.s["orders"][0]["level"] = 10.0
            with patch.object(strategy, "notify"):
                engine._reconcile_real(100.0)
        self.assertEqual(engine.s["orders"], [])
        self.assertEqual(engine.s["tp_sold_levels"], [])

    def test_partial_dca_counts_one_buy_across_multiple_reconciliations(self):
        client = _FillClient()
        client.active = [{"id": "BUY-1", "ticker": "TEST_US_EQ"}]
        client.portfolio = [{
            "ticker": "TEST_US_EQ", "quantity": 1.5,
            "averagePrice": 145.0 / 1.5,
        }]
        client.status = {
            "id": "BUY-1", "ticker": "TEST_US_EQ", "status": "PARTIALLY_FILLED",
            "filledQuantity": 0.5, "filledValue": 45.0,
        }
        with tempfile.TemporaryDirectory() as audit_dir:
            engine = self._engine(client, audit_dir)
            engine.s["orders"] = [{
                "id": "BUY-1", "side": "BUY", "qty": 1.0,
                "limit": 90.0, "amount": 90.0, "kind": "DCA",
                "intent_id": "t212-test-dca", "ts": 0.0,
            }]
            with patch.object(strategy, "notify"):
                engine._reconcile_real(90.0)
            self.assertEqual(engine.s["dca_buys"], 1)

            client.portfolio = [{
                "ticker": "TEST_US_EQ", "quantity": 1.75,
                "averagePrice": 167.5 / 1.75,
            }]
            client.status.update(filledQuantity=0.75, filledValue=67.5)
            with patch.object(strategy, "notify"):
                engine._reconcile_real(90.0)
        self.assertEqual(engine.s["dca_buys"], 1)

    def test_fill_racing_with_accepted_cancel_is_still_reconciled_exactly(self):
        client = _FillClient()
        client.portfolio = [{
            "ticker": "TEST_US_EQ", "quantity": 1.0, "averagePrice": 100.0,
        }]
        client.active = [{"id": "SELL-1", "ticker": "TEST_US_EQ"}]
        client.status = {
            "id": "SELL-1", "ticker": "TEST_US_EQ", "status": "CONFIRMED",
            "filledQuantity": 0.0, "filledValue": 0.0,
        }
        with tempfile.TemporaryDirectory() as audit_dir:
            engine = self._engine(client, audit_dir)
            order = engine.s["orders"][0]

            self.assertTrue(engine._cancel_specific(order))
            self.assertIn(order, engine.s["orders"])

            # O jumatate se executa chiar in cursa cu anularea. Statusul terminal
            # trebuie citit inainte sa uitam ordinul, altfel P&L-ul ar folosi poll price.
            client.portfolio = [{
                "ticker": "TEST_US_EQ", "quantity": 0.5, "averagePrice": 100.0,
            }]
            client.active = []
            client.status = {
                "id": "SELL-1", "ticker": "TEST_US_EQ", "status": "CANCELLED",
                "filledQuantity": 0.5, "filledValue": 55.0,
            }
            with patch.object(strategy, "notify"):
                engine._reconcile_real(150.0)

        self.assertEqual(engine.s["orders"], [])
        self.assertAlmostEqual(engine.s["qty"], 0.5)
        self.assertAlmostEqual(engine.s["realized_pnl_usd"], 5.0)
        self.assertAlmostEqual(engine.s["last_sell_price"], 110.0)


class _CancelClient:
    def __init__(self, cancel_result=False):
        self.cancel_result = cancel_result
        self.limit_result = None
        self.market_result = None
        self.cancel_calls = []
        self.place_calls = []

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        if isinstance(self.cancel_result, Exception):
            raise self.cancel_result
        return self.cancel_result

    def place_limit_order(self, ticker, quantity, limit, validity):
        self.place_calls.append(("limit", ticker, quantity, limit, validity))
        if self.limit_result is not None:
            return self.limit_result
        return 200, {"id": f"NEW-{len(self.place_calls)}"}

    def place_market_order(self, ticker, quantity, extended_hours=False):
        self.place_calls.append(("market", ticker, quantity, extended_hours))
        if self.market_result is not None:
            return self.market_result
        return 200, {"id": f"NEW-{len(self.place_calls)}"}


class T212CancellationLifecycleTest(unittest.TestCase):
    @staticmethod
    def _engine(client, **param_overrides):
        engine = strategy.Strategy(
            client, "TEST_US_EQ", _params(**param_overrides), dry_run=False,
            initial_state=strategy._new_state(), fx_to_usd=1.0,
        )
        engine._save = lambda: None
        return engine

    @staticmethod
    def _order(**overrides):
        order = {
            "id": "OLD-1", "side": "SELL", "qty": 1.0,
            "limit": 110.0, "kind": "TP", "ts": 0.0,
        }
        order.update(overrides)
        return order

    def test_failed_or_raised_cancel_keeps_order_tracked(self):
        for result in (False, RuntimeError("timeout")):
            with self.subTest(result=result):
                client = _CancelClient(result)
                engine = self._engine(client)
                order = self._order()
                engine.s["orders"] = [order]

                self.assertFalse(engine._cancel_specific(order))
                self.assertEqual(engine.s["orders"], [order])

    def test_ladder_does_not_replace_an_order_whose_cancel_failed(self):
        for old in (self._order(level=10.0), self._order()):
            with self.subTest(order=old):
                client = _CancelClient(False)
                engine = self._engine(client, STRAT_TP_LADDER="10:100")
                engine.s["orders"] = [old]

                engine._manage_tp_ladder(held=1.0, avg=100.0)

                self.assertEqual(engine.s["orders"], [old])
                self.assertEqual(client.place_calls, [])

    def test_stop_waits_for_confirmed_cancels_then_places_one_exit(self):
        client = _CancelClient(False)
        engine = self._engine(client)
        old = self._order(side="BUY", kind="DCA", limit=90.0)
        engine.s.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
            "orders": [old],
        })

        with patch.object(strategy, "notify"):
            self.assertTrue(engine._check_stop_loss(70.0))
        self.assertEqual(engine.s["orders"], [old])
        self.assertEqual(client.place_calls, [])

        client.cancel_result = True
        with patch.object(strategy, "notify"):
            self.assertTrue(engine._check_stop_loss(70.0))
        self.assertEqual(len(client.place_calls), 1)
        self.assertEqual(client.place_calls[0][0], "market")
        self.assertEqual(len(engine.s["orders"]), 2)
        self.assertTrue(old["cancel_requested"])
        stop = next(o for o in engine.s["orders"] if o.get("kind") == "SL")
        self.assertTrue(stop["market"])

    def test_accepted_cancel_stays_tracked_and_is_not_submitted_twice(self):
        client = _CancelClient(True)
        engine = self._engine(client)
        order = self._order()
        engine.s["orders"] = [order]

        self.assertTrue(engine._cancel_specific(order))
        self.assertTrue(engine._cancel_specific(order))

        self.assertEqual(engine.s["orders"], [order])
        self.assertTrue(order["cancel_requested"])
        self.assertEqual(client.cancel_calls, ["OLD-1"])

    def test_ladder_waits_for_terminal_cancel_before_replacement(self):
        client = _CancelClient(True)
        engine = self._engine(client, STRAT_TP_LADDER="10:100")
        old = self._order(level=10.0, qty=0.5, limit=109.0)
        engine.s["orders"] = [old]

        engine._manage_tp_ladder(held=1.0, avg=100.0)

        self.assertTrue(old["cancel_requested"])
        self.assertEqual(engine.s["orders"], [old])
        self.assertEqual(client.place_calls, [])

    def test_live_submit_and_cancel_are_persisted_immediately(self):
        client = _CancelClient(True)
        engine = self._engine(client)
        engine._save = MagicMock()

        self.assertTrue(engine._place_sell(1.0, 110.0))
        engine._save.assert_called_once()

        engine._save.reset_mock()
        order = engine.s["orders"][0]
        self.assertTrue(engine._cancel_specific(order))
        engine._save.assert_called_once()

    def test_trailing_exit_is_market_and_is_not_replaced_while_pending(self):
        client = _CancelClient(True)
        engine = self._engine(
            client, STRAT_TRAIL_PCT="5", STRAT_TRAIL_MIN_PROFIT_PCT="0",
        )
        engine.s.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
            "pos_peak": 120.0, "tr_armed": True,
        })

        with patch.object(strategy, "notify"):
            self.assertTrue(engine._check_trailing(110.0))
            self.assertTrue(engine._check_trailing(105.0))

        self.assertEqual(len(client.place_calls), 1)
        self.assertEqual(client.place_calls[0][0], "market")
        self.assertEqual(engine.s["orders"][0]["kind"], "TR")
        self.assertTrue(engine.s["orders"][0]["market"])

    def test_rejected_market_exit_does_not_arm_rebuy_or_claim_success(self):
        for check, overrides, state in (
            ("_check_stop_loss", {}, {}),
            (
                "_check_trailing",
                {"STRAT_TRAIL_PCT": "5", "STRAT_TRAIL_MIN_PROFIT_PCT": "0"},
                {"pos_peak": 120.0, "tr_armed": True},
            ),
        ):
            with self.subTest(check=check):
                client = _CancelClient(True)
                client.market_result = (500, {"error": "rejected"})
                engine = self._engine(client, **overrides)
                engine.s.update({
                    "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
                    **state,
                })

                with patch.object(strategy, "notify") as notify:
                    self.assertTrue(getattr(engine, check)(70.0))

                self.assertFalse(engine.s.get("sl_pending", False))
                self.assertEqual(engine.s["orders"], [])
                notify.assert_not_called()

    def test_ambiguous_not_owned_error_never_erases_local_position(self):
        client = _CancelClient(True)
        client.limit_result = (400, {"code": "selling-equity-not-owned"})
        engine = self._engine(client)
        engine.s.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
        })

        self.assertFalse(engine._place_sell(1.0, 110.0))

        self.assertEqual(engine.s["qty"], 1.0)
        self.assertEqual(engine.s["cost_usd"], 100.0)
        self.assertEqual(engine.s["spent_cash"], 100.0)

    def test_limit_orders_keep_the_profile_validity(self):
        client = _CancelClient(True)
        engine = self._engine(client)

        self.assertTrue(engine._place_sell(1.0, 110.0))

        self.assertEqual(client.place_calls[-1][-1], "GOOD_TILL_CANCEL")


if __name__ == "__main__":
    unittest.main()

"""
Teste pentru Instrument.place() — cele 4 protectii AGNOSTICE nou-adaugate (30 iul,
cerere user: "guardrail-urile din bapi_placeorder ca implementare comuna pt toti
providerii"): plafon zilnic + anti-spam (order_guard.daily_limit_guard), cooldown
anti-rapid-fire (lock/trade_cooldown, deja generic), trend-gate instantaneu
(cacheManager) si jurnalul FLEET-WIDE
(order_outcomes_log). Se aplica DOAR providerilor cu guards_internally()==False
(Binance ramane neatins, isi pastreaza propria implementare — vezi bapi_placeorder.py).

Provider FALS (nu Binance/Kraken reali) — izoleaza complet de retea; `guards_internally`
implicit False, exact ca Kraken/Hyperliquid azi.
"""
import os
import sys
import time
import glob
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.market_api import MarketApi, MarketDataProvider
from instrument import Instrument
import order_outcomes_log as outcomes_log
from lock import trade_cooldown as tc

SYMBOL = "ZZZFAKEUSD"


class _FakeProvider(MarketDataProvider):
    """Provider minimal, in-memorie — guards_internally()=False (ca Kraken/HL)."""

    def __init__(self, name="FakeVenue", price=100.0):
        self._name = name
        self._price = price
        self._orders = []   # [{"side","price","qty","timestamp"(ms)}]
        self.placed = []    # REAL calls to place_order (after every gate)
        self.status_calls = []

    @property
    def name(self):
        return self._name

    def get_current_price(self, symbol):
        return self._price

    def supports_symbol(self, symbol):
        return False

    def free_balance(self, asset):
        return 1_000_000.0   # suficient cat sa nu limiteze artificial qty in teste

    def get_orders(self, symbol, side, since_s):
        cutoff_ms = time.time() * 1000 - since_s * 1000
        out = [o for o in self._orders
              if o["symbol"] == symbol and o["timestamp"] >= cutoff_ms]
        if side:
            out = [o for o in out if o["side"] == side.upper()]
        return out

    def place_order(self, symbol, side, price, qty, **kwargs):
        self.placed.append((symbol, side, price, qty, kwargs))
        return {"orderId": len(self.placed)}

    def order_status(self, symbol, order_id):
        self.status_calls.append((symbol, order_id))
        raise AssertionError("Instrument.place must not poll terminal status")

    def guards_internally(self):
        return False

    def seed_trade(self, side, age_sec=0.0, price=100.0, qty=1.0, symbol=SYMBOL):
        self._orders.append({"symbol": symbol, "side": side.upper(), "price": price,
                             "qty": qty, "timestamp": (time.time() - age_sec) * 1000})


class _GuardsInternallyProvider(_FakeProvider):
    """Ca Binance: are deja propriul lant de gard -> Instrument.place() trebuie sa
    SKIPS all 4 agnostic protections entirely (no cooldown, no daily limit)."""
    def guards_internally(self):
        return True


class InstrumentGuardsTestCase(unittest.TestCase):
    def setUp(self):
        # Cooldown: state/lock izolate (tiparul din test_trade_cooldown.py) — altfel
        # testul ar atinge lock/trade_cooldown.json REAL.
        self._tmp = tempfile.mkdtemp()
        tc.STATE_FILE = os.path.join(self._tmp, "trade_cooldown.json")
        tc.LOCK_FILE = os.path.join(self._tmp, "trade_cooldown.lock")
        # Jurnal outcome: director izolat — altfel testul ar scrie in logger/ REAL,
        # citit de tradeall_observe.py in productie.
        self._log_tmp = tempfile.mkdtemp()
        self._orig_log_dir = outcomes_log.ORDER_OUTCOMES_LOG_DIR
        outcomes_log.ORDER_OUTCOMES_LOG_DIR = self._log_tmp
        # Coada de re-plasare: izolata — Instrument.place() face enqueue pe esec, iar
        # aceste teste declanseaza multe esecuri asteptate (cooldown/daily-limit) -> NU
        # trebuie sa polueze cachedb/order_retry_queue.jsonl real.
        import order_retry as _oq
        _oq.QUEUE_FILE = os.path.join(self._tmp, "order_retry_queue.jsonl")
        _oq.LOCK_FILE = os.path.join(self._tmp, "order_retry_queue.lock")
        # Explicit pin — the tests must not depend on the kill switch in the live config
        _oq.RETRY_ENABLED = True
        _oq.RETRY_DEDUP = True

    def tearDown(self):
        outcomes_log.ORDER_OUTCOMES_LOG_DIR = self._orig_log_dir

    def _inst(self, provider):
        api = MarketApi([provider])
        return Instrument(name="ZZZFAKE", symbol=SYMBOL, provider=provider.name.lower(),
                          base="ZZZFAKE", quote="USD", api=api)

    def _log_lines(self):
        files = glob.glob(os.path.join(self._log_tmp, "order_outcomes_*.log"))
        lines = []
        for f in files:
            with open(f) as fh:
                lines.extend(fh.read().splitlines())
        return lines

    def test_explicit_balance_lookup_does_not_route_by_bare_asset(self):
        first = _FakeProvider(name="First")
        second = _FakeProvider(name="Second")
        first.free_balance = lambda _asset: 11.0
        second.free_balance = lambda _asset: 22.0
        api = MarketApi([first, second])

        self.assertEqual(api.free_balance_for("second", "USDC"), 22.0)
        with self.assertRaisesRegex(ValueError, "Provider necunoscut"):
            api.free_balance_for("missing", "USDC")

    # ── plafon zilnic + anti-spam ───────────────────────────────────────────────
    def test_daily_limit_blocks_after_threshold(self):
        p = _FakeProvider()
        inst = self._inst(p)
        # safeback explicit (48h) ca testul sa fie independent de defaultul din config
        # (schimbat 30 iul la 14 zile). backdays = ceil(48h/86400) = 3 -> prag 25*3=75;
        # 90 tranzactii vechi (>3min, sub pragul anti-spam) il depasesc.
        for _ in range(90):
            p.seed_trade("BUY", age_sec=4000.0)
        order = inst.place("BUY", 100.0, 1.0, safeback_seconds=48 * 3600 + 60)
        self.assertIsNone(order)
        self.assertEqual(p.placed, [])
        lines = self._log_lines()
        self.assertTrue(any("|refused|daily_limit|" in l for l in lines), lines)

    def test_safeback_seconds_default_window_misses_old_trades(self):
        # 30 iul, fix: monitortrades.py (sbs=MT_GUARD_WINDOW_DAYS, implicit 12 ZILE) si
        # tradeall.py (14 zile) suprascriu safeback_seconds la fiecare apel real — defaultul
        # din config (48h) e aproape niciodata folosit efectiv. instruments.conf are deja
        # [KRAKEN_HYPE] enabled=yes under "mt", so the same sbs applies there too.
        p = _FakeProvider()
        inst = self._inst(p)
        for _ in range(60):
            p.seed_trade("BUY", age_sec=5 * 24 * 3600)   # 5 zile in urma -> AFARA din 48h implicit
        order = inst.place("BUY", 100.0, 1.0)   # No override -> the default (48h) does not see them
        self.assertIsNotNone(order)

    def test_safeback_seconds_override_sees_older_trades_and_blocks(self):
        p = _FakeProvider()
        inst = self._inst(p)
        # backdays = math.ceil(14 zile + 60s / 86400) = 15 zile (rotunjit in SUS) ->
        # prag 25*15=375; 400 tranzactii il depasesc.
        for _ in range(400):
            p.seed_trade("BUY", age_sec=5 * 24 * 3600)   # 5 zile in urma
        # override explicit de 14 zile (identic cu tradeall.py: d=14, h=24) -> ACUM le vede -> blocat
        order = inst.place("BUY", 100.0, 1.0, safeback_seconds=14 * 24 * 3600 + 60)
        self.assertIsNone(order)

    def test_recent_transaction_blocks(self):
        p = _FakeProvider()
        p.seed_trade("BUY", age_sec=5.0)   # acum 5s, sub pragul implicit de 180s
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNone(order)
        lines = self._log_lines()
        self.assertTrue(any("|refused|recent_transaction|" in l for l in lines), lines)

    def test_bypass_profit_guard_does_not_skip_daily_limit(self):
        p = _FakeProvider()
        for _ in range(90):   # vezi test_daily_limit_blocks_after_threshold (safeback 48h -> prag 75)
            p.seed_trade("BUY", age_sec=4000.0)
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0, safeback_seconds=48 * 3600 + 60, bypass_profit_guard=True)
        self.assertIsNone(order)   # plafonul zilnic ramane activ chiar si cu bypass

    def test_reference_only_bypass_keeps_quantity_policy_active(self):
        quantity_calls = []

        class _QuantityCapProvider(_FakeProvider):
            def policy_cap_quantity(self, symbol, side, price, qty, available_qty,
                                    **kwargs):
                quantity_calls.append((symbol, side, price, qty, available_qty))
                return 0.25

        p = _QuantityCapProvider()
        p.seed_trade("SELL", age_sec=400.0, price=100.0)
        inst = self._inst(p)

        blocked = inst.place(
            "BUY", 101.0, 1.0, smart=False, caller_owns_retry=True)
        allowed = inst.place(
            "BUY", 101.0, 1.0, smart=False, caller_owns_retry=True,
            bypass_profit_reference=True)

        self.assertIsNone(blocked, "the normal guard must block a BUY above a SELL")
        self.assertIsNotNone(allowed)
        self.assertEqual(len(quantity_calls), 1)
        self.assertEqual(p.placed[0][3], 0.25)

    def test_reference_only_bypass_does_not_skip_daily_limit(self):
        p = _FakeProvider()
        for _ in range(90):
            p.seed_trade("BUY", age_sec=4000.0)
        order = self._inst(p).place(
            "BUY", 100.0, 1.0,
            safeback_seconds=48 * 3600 + 60,
            bypass_profit_reference=True,
            caller_owns_retry=True)
        self.assertIsNone(order)
        self.assertEqual(p.placed, [])

    def test_reference_only_bypass_is_ignored_for_sell(self):
        p = _FakeProvider()
        p.seed_trade("BUY", age_sec=400.0, price=100.0)

        order = self._inst(p).place(
            "SELL", 99.0, 1.0,
            smart=False,
            bypass_profit_reference=True,
            caller_owns_retry=True)

        self.assertIsNone(order, "SELL sub referinta BUY trebuie blocat")
        self.assertEqual(p.placed, [])
        lines = self._log_lines()
        self.assertTrue(any("|refused|profit_guard|" in line for line in lines), lines)

    def test_quantity_policy_bypass_is_sell_only_and_keeps_profit_guard(self):
        policy_calls = []

        class _ZeroPolicyProvider(_FakeProvider):
            def policy_cap_quantity(self, *args, **kwargs):
                policy_calls.append((args, kwargs))
                return 0.0

        p = _ZeroPolicyProvider()
        p.seed_trade("BUY", age_sec=400.0, price=100.0)
        inst = self._inst(p)

        loss = inst.place(
            "SELL", 99.0, 1.0, smart=False, caller_owns_retry=True,
            bypass_quantity_policy=True)
        profit = inst.place(
            "SELL", 102.0, 1.0, smart=False, caller_owns_retry=True,
            bypass_quantity_policy=True)

        self.assertIsNone(loss, "the quantity bypass must not skip the profit guard")
        self.assertIsNotNone(profit)
        self.assertEqual(policy_calls, [], "weight policy trebuie sarita numai la SELL")
        self.assertEqual(p.placed[0][3], 1.0)

    def test_quantity_policy_bypass_is_ignored_for_buy(self):
        class _ZeroPolicyProvider(_FakeProvider):
            def policy_cap_quantity(self, *args, **kwargs):
                return 0.0

        p = _ZeroPolicyProvider()
        p.seed_trade("SELL", age_sec=400.0, price=100.0)
        order = self._inst(p).place(
            "BUY", 98.0, 1.0, smart=False, caller_owns_retry=True,
            bypass_quantity_policy=True)

        self.assertIsNone(order)
        self.assertEqual(p.placed, [])

    def test_quantity_policy_bypass_keeps_balance_and_fee_caps(self):
        class _FeeCapProvider(_FakeProvider):
            def free_balance(self, _asset):
                return 1.0

            def policy_cap_quantity(self, *args, **kwargs):
                raise AssertionError("policy must be bypassed")

            def fee_cap_quantity(self, symbol, side, price, available_qty):
                return 0.9

        p = _FeeCapProvider()
        p.seed_trade("BUY", age_sec=400.0, price=100.0)
        order = self._inst(p).place(
            "SELL", 102.0, 2.0, smart=False, caller_owns_retry=True,
            bypass_quantity_policy=True)

        self.assertIsNotNone(order)
        self.assertEqual(p.placed[0][3], 0.9)

    def test_first_order_allowed_and_logged(self):
        p = _FakeProvider()
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        self.assertEqual(len(p.placed), 1)
        lines = self._log_lines()
        self.assertTrue(any("|accepted|" in l and SYMBOL in l for l in lines), lines)

    def test_intent_is_persisted_with_same_client_id_before_provider_submit(self):
        import order_retry as oq

        class _InspectProvider(_FakeProvider):
            def place_order(self, symbol, side, price, qty, **kwargs):
                queued = oq.load_all()
                self.persisted_during_submit = copy = dict(queued[0])
                self.submitted_client_id = kwargs.get("client_order_id")
                return {"orderId": "persisted-1"}

        p = _InspectProvider()
        order = self._inst(p).place("BUY", 100.0, 1.0)

        self.assertEqual(order["orderId"], "persisted-1")
        self.assertEqual(
            p.persisted_during_submit["place_kwargs"]["client_order_id"],
            p.submitted_client_id)
        tracked = oq.load_all()
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["lifecycle"], "accepted")
        self.assertEqual(tracked[0]["order_id"], "persisted-1")

    def test_truthy_payload_without_order_id_is_refused_and_remains_queued(self):
        import order_retry as oq

        class _AmbiguousProvider(_FakeProvider):
            def place_order(self, symbol, side, price, qty, **kwargs):
                self.placed.append((symbol, side, price, qty, kwargs))
                return {"status": "UNKNOWN"}

        p = _AmbiguousProvider()
        order = self._inst(p).place("BUY", 100.0, 1.0)

        self.assertIsNone(order)
        queued = oq.load_all()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["last_failure_reason"],
                         "response_without_order_id")
        self.assertTrue(any("|refused|response_without_order_id|" in line
                            for line in self._log_lines()))

    def test_submit_response_loss_keeps_pre_submit_intent_with_same_client_id(self):
        import order_retry as oq

        class _LostResponseProvider(_FakeProvider):
            def place_order(self, symbol, side, price, qty, **kwargs):
                self.submitted_client_id = kwargs.get("client_order_id")
                raise TimeoutError("response lost after possible venue acceptance")

        p = _LostResponseProvider()
        order = self._inst(p).place("BUY", 100.0, 1.0)

        self.assertIsNone(order)
        queued = oq.load_all()
        self.assertEqual(len(queued), 1)
        self.assertEqual(
            queued[0]["place_kwargs"]["client_order_id"],
            p.submitted_client_id)
        self.assertEqual(queued[0]["last_failure_reason"], "submit_ambiguous")

    # ── cooldown anti-rapid-fire ────────────────────────────────────────────────
    def test_cooldown_blocks_second_order(self):
        p = _FakeProvider()
        inst = self._inst(p)
        first = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(first)
        second = inst.place("SELL", 101.0, 1.0)   # < cooldown_sec dupa primul
        self.assertIsNone(second)
        self.assertEqual(len(p.placed), 1)   # doar primul a ajuns la provider
        lines = self._log_lines()
        self.assertTrue(any("|refused|cooldown|" in l for l in lines), lines)

    def test_cooldown_independent_per_symbol(self):
        p = _FakeProvider()
        inst_a = Instrument(name="A", symbol="ZZZFAKEUSD_A", provider=p.name.lower(),
                            base="A", quote="USD", api=MarketApi([p]))
        inst_b = Instrument(name="B", symbol="ZZZFAKEUSD_B", provider=p.name.lower(),
                            base="B", quote="USD", api=MarketApi([p]))
        self.assertIsNotNone(inst_a.place("BUY", 100.0, 1.0))
        self.assertIsNotNone(inst_b.place("BUY", 100.0, 1.0))   # alt symbol -> neafectat

    def test_pair_id_allows_only_the_opposite_leg_through_cooldown(self):
        p = _FakeProvider()
        inst = self._inst(p)

        buy = inst.place(
            "BUY", 99.0, 1.0, smart=False,
            cooldown_pair_id="pair-1", caller_owns_retry=True)
        sell = inst.place(
            "SELL", 101.0, 1.0, smart=False,
            cooldown_pair_id="pair-1", caller_owns_retry=True)
        duplicate = inst.place(
            "SELL", 102.0, 1.0, smart=False,
            cooldown_pair_id="pair-1", caller_owns_retry=True)

        self.assertIsNotNone(buy)
        self.assertIsNotNone(sell)
        self.assertIsNone(duplicate)
        self.assertEqual(len(p.placed), 2)

    def test_facade_place_routes_through_pipeline(self):
        # MarketApi.place() (proxy unic guardat, inlocuitorul lui place_order_smart):
        # construieste Instrument efemer + ruleaza pipeline-ul (cooldown blocheaza al 2-lea).
        p = _FakeProvider()
        mkt = MarketApi([p])
        first = mkt.place(SYMBOL, "BUY", 100.0, 1.0)
        self.assertIsNotNone(first)
        self.assertEqual(len(p.placed), 1)
        second = mkt.place(SYMBOL, "SELL", 101.0, 1.0)   # < cooldown -> blocat
        self.assertIsNone(second)
        self.assertEqual(len(p.placed), 1)

    def test_failed_order_enqueued_for_retry(self):
        import order_retry as _oq
        p = _FakeProvider()
        p.seed_trade("BUY", age_sec=5.0)   # anti-spam -> refuz
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNone(order)
        q = _oq.load_all()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["symbol"], SYMBOL)
        self.assertEqual(q[0]["side"], "BUY")

    def test_retry_flag_prevents_reenqueue(self):
        import order_retry as _oq
        p = _FakeProvider()
        p.seed_trade("BUY", age_sec=5.0)   # refuz
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0, caller_owns_retry=True)
        self.assertIsNone(order)
        self.assertEqual(_oq.load_all(), [])   # It is NOT re-enqueued (no recursion)

    def test_success_remains_tracked_until_terminal(self):
        import order_retry as _oq
        p = _FakeProvider()
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        tracked = _oq.load_all()
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["lifecycle"], "accepted")
        self.assertEqual(tracked[0]["order_id"], "1")
        self.assertEqual(p.status_calls, [])

    def test_success_does_not_remove_an_independent_same_side_intent(self):
        import order_retry as _oq
        _oq.RETRY_DEDUP = False
        _oq.enqueue(SYMBOL, "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        _oq.enqueue(SYMBOL, "SELL", 1.0, {}, requested_price=101.0, now=1001.0)

        p = _FakeProvider()
        order = self._inst(p).place("BUY", 100.0, 1.0)

        self.assertIsNotNone(order)
        remaining = _oq.load_all()
        self.assertEqual(len(remaining), 3)
        buys = [row for row in remaining if row["side"] == "BUY"]
        sells = [row for row in remaining if row["side"] == "SELL"]
        self.assertEqual(len(buys), 2)
        self.assertEqual(len(sells), 1)
        self.assertEqual(
            sorted(row["lifecycle"] for row in buys),
            ["accepted", "submit_pending"],
        )

    def test_smart_flag_gates_price_adjust(self):
        # CORECTIE 30 iul: place_order_smart (SMART: cancel-opuse + nudge) vs place_safe_order
        # (SAFE: none). smart=True calls adjust_order_price; smart=False does NOT (it keeps
        # fostului place_safe_order pt rtrade normal/assetguardian/trailing_stop/monitororder).
        calls = []

        class _SpyProvider(_FakeProvider):
            def adjust_order_price(self, symbol, side, price, cancel_opposite=True):
                calls.append((symbol, side, cancel_opposite))
                return price

        p = _SpyProvider()
        inst = Instrument(name="ZZZFAKE", symbol=SYMBOL, provider=p.name.lower(),
                          base="ZZZFAKE", quote="USD", api=MarketApi([p]))
        inst.place("BUY", 100.0, 1.0, smart=True)
        self.assertEqual(len(calls), 1, "smart=True trebuie sa cheme adjust_order_price")
        calls.clear()
        # cooldown ar bloca al 2-lea pe acelasi symbol -> alt symbol pt smart=False
        inst2 = Instrument(name="ZZZFAKE2", symbol="ZZZFAKEUSD2", provider=p.name.lower(),
                           base="ZZZFAKE2", quote="USD", api=MarketApi([p]))
        inst2.place("BUY", 100.0, 1.0, smart=False)
        self.assertEqual(calls, [], "smart=False must NOT call adjust_order_price")

    def test_cancelorders_and_hours_reach_quantity_hook(self):
        calls = []

        class _QuantitySpyProvider(_FakeProvider):
            def policy_cap_quantity(self, symbol, side, price, qty, available_qty,
                                    base=None, quote=None, cancelorders=False, hours=5):
                calls.append((cancelorders, hours))
                return qty

        p = _QuantitySpyProvider()
        order = self._inst(p).place(
            "BUY", 100.0, 1.0, smart=False, cancelorders=True, hours=2.7)

        self.assertIsNotNone(order)
        self.assertEqual(calls, [(True, 2.7)])

    # ── fidelitate financiara MARKET ──────────────────────────────────────────
    def test_market_order_profit_guard_uses_current_price_not_ignored_target(self):
        p = _FakeProvider(price=100.0)
        # Ultimul BUY este referinta SELL. Tinta declarata 102 trece marja de
        # 1.15%, but MARKET would fill at 100 and only produce costs.
        p.seed_trade("BUY", age_sec=400.0, price=100.0)

        order = self._inst(p).place("SELL", 102.0, 1.0, force=True, smart=False)

        self.assertIsNone(order)
        self.assertEqual(p.placed, [])
        self.assertTrue(any("|refused|profit_guard|" in line for line in self._log_lines()))

    def test_limit_order_keeps_profitable_target_price(self):
        p = _FakeProvider(price=100.0)
        p.seed_trade("BUY", age_sec=400.0, price=100.0)

        order = self._inst(p).place("SELL", 102.0, 1.0, force=False, smart=False)

        self.assertIsNotNone(order)
        self.assertEqual(p.placed[0][2], 102.0)

    def test_protective_market_bypass_remains_explicitly_allowed(self):
        p = _FakeProvider(price=90.0)
        p.seed_trade("BUY", age_sec=400.0, price=100.0)

        order = self._inst(p).place(
            "SELL", 102.0, 1.0, force=True, smart=False,
            bypass_profit_guard=True)

        self.assertIsNotNone(order)
        self.assertTrue(p.placed[0][4]["force"])

    def test_market_order_allowed_only_when_current_price_meets_margin(self):
        p = _FakeProvider(price=102.0)
        p.seed_trade("BUY", age_sec=400.0, price=100.0)

        order = self._inst(p).place("SELL", 102.0, 1.0, force=True, smart=False)

        self.assertIsNotNone(order)
        self.assertTrue(p.placed[0][4]["force"])

    def test_trend_deferral_returns_immediately_without_wait_loop(self):
        class _Deferred:
            @staticmethod
            def should_wait(_side, _symbol):
                return True

            @staticmethod
            def wait_for_favorable_entry(*_args, **_kwargs):
                raise AssertionError("Instrument.place must not enter a wait loop")

        p = _FakeProvider(price=100.0)
        p.seed_trade("BUY", age_sec=400.0, price=100.0)
        with patch("cacheManager.get_short_trend_manager", return_value=_Deferred()):
            order = self._inst(p).place("SELL", 102.0, 1.0, force=False, smart=False)

        self.assertIsNone(order)
        self.assertEqual(p.placed, [])
        self.assertTrue(any("|refused|trend_deferred|" in line
                            for line in self._log_lines()))

    def test_trend_deferral_with_auto_quantity_enters_outbox_with_numeric_qty(self):
        import order_retry as oq

        class _Deferred:
            @staticmethod
            def should_wait(_side, _symbol):
                return True

        p = _FakeProvider(price=100.0)
        with patch("cacheManager.get_short_trend_manager", return_value=_Deferred()):
            order = self._inst(p).place("BUY", 100.0, None, smart=False)

        self.assertIsNone(order)
        queued = oq.load_all()
        self.assertEqual(len(queued), 1)
        self.assertGreater(float(queued[0]["qty"]), 0.0)
        self.assertEqual(queued[0]["last_failure_reason"], "trend_deferred")

    def test_tracked_caller_can_disable_instantaneous_trend_gate(self):
        p = _FakeProvider(price=102.0)
        p.seed_trade("BUY", age_sec=400.0, price=100.0)
        with patch("cacheManager.get_short_trend_manager") as manager:
            order = self._inst(p).place(
                "SELL", 102.0, 1.0, force=False, smart=False,
                wait_for_trend=False)

        self.assertIsNotNone(order)
        manager.assert_not_called()

    # ── guards_internally (Binance-style) — sare TOT stratul agnostic ───────────
    def test_guards_internally_provider_bypasses_new_gates(self):
        p = _GuardsInternallyProvider()
        inst = self._inst(p)
        # A seed that WOULD trip the daily limit if it applied -> it must not block
        for _ in range(60):
            p.seed_trade("BUY", age_sec=4000.0)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        self.assertEqual(len(p.placed), 1)
        # niciun log FLEET-WIDE nou (Binance isi loga singur, ca sa nu duplice)
        self.assertEqual(self._log_lines(), [])
        # The second immediate placement is NOT blocked by the cooldown (guards_internally skips it)
        order2 = inst.place("SELL", 101.0, 1.0)
        self.assertIsNotNone(order2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

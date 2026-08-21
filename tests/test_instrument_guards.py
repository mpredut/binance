"""
Teste pentru Instrument.place() — cele 4 protectii AGNOSTICE nou-adaugate (30 iul,
cerere user: "guardrail-urile din bapi_placeorder ca implementare comuna pt toti
providerii"): plafon zilnic + anti-spam (order_guard.daily_limit_guard), cooldown
anti-rapid-fire (lock/trade_cooldown, deja generic), trend-wait (cacheManager,
deja generic — cade pe "nu astepta" pt un symbol necunoscut) si jurnalul FLEET-WIDE
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
        self.placed = []    # apeluri REALE catre place_order (dupa toate gate-urile)

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

    def guards_internally(self):
        return False

    def seed_trade(self, side, age_sec=0.0, price=100.0, qty=1.0, symbol=SYMBOL):
        self._orders.append({"symbol": symbol, "side": side.upper(), "price": price,
                             "qty": qty, "timestamp": (time.time() - age_sec) * 1000})


class _GuardsInternallyProvider(_FakeProvider):
    """Ca Binance: are deja propriul lant de gard -> Instrument.place() trebuie sa
    SARA complet peste cele 4 protectii agnostice (fara cooldown, fara daily-limit)."""
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
        # pin explicit — testele nu trebuie sa depinda de kill-switch-ul din config live
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
        # [KRAKEN_HYPE] enabled=yes sub "mt", deci acelasi sbs se aplica si acolo.
        p = _FakeProvider()
        inst = self._inst(p)
        for _ in range(60):
            p.seed_trade("BUY", age_sec=5 * 24 * 3600)   # 5 zile in urma -> AFARA din 48h implicit
        order = inst.place("BUY", 100.0, 1.0)   # fara override -> defaultul (48h) nu le vede
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

    def test_first_order_allowed_and_logged(self):
        p = _FakeProvider()
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        self.assertEqual(len(p.placed), 1)
        lines = self._log_lines()
        self.assertTrue(any("|executed|" in l and SYMBOL in l for l in lines), lines)

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
        order = inst.place("BUY", 100.0, 1.0, is_retry=True)   # e deja un retry
        self.assertIsNone(order)
        self.assertEqual(_oq.load_all(), [])   # NU se re-enqueue-aza (fara recursie)

    def test_success_not_enqueued(self):
        import order_retry as _oq
        p = _FakeProvider()
        inst = self._inst(p)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        self.assertEqual(_oq.load_all(), [])   # succes -> nimic in coada

    def test_success_resolves_stale_same_side_retry(self):
        import order_retry as _oq
        _oq.enqueue(SYMBOL, "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        _oq.enqueue(SYMBOL, "SELL", 1.0, {}, requested_price=101.0, now=1001.0)

        p = _FakeProvider()
        order = self._inst(p).place("BUY", 100.0, 1.0)

        self.assertIsNotNone(order)
        remaining = _oq.load_all()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["side"], "SELL")

    def test_smart_flag_gates_price_adjust(self):
        # CORECTIE 30 iul: place_order_smart (SMART: cancel-opuse + nudge) vs place_safe_order
        # (SAFE: fara). smart=True cheama adjust_order_price; smart=False NU (pastreaza semantica
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
        self.assertEqual(calls, [], "smart=False NU trebuie sa cheme adjust_order_price")

    def test_cancelorders_and_hours_reach_quantity_hook(self):
        calls = []

        class _QuantitySpyProvider(_FakeProvider):
            def cap_quantity(self, symbol, side, price, qty, base=None, quote=None,
                             cancelorders=False, hours=5):
                calls.append((cancelorders, hours))
                return qty

        p = _QuantitySpyProvider()
        order = self._inst(p).place(
            "BUY", 100.0, 1.0, smart=False, cancelorders=True, hours=2.7)

        self.assertIsNotNone(order)
        self.assertEqual(calls, [(True, 2.7)])

    # ── guards_internally (Binance-style) — sare TOT stratul agnostic ───────────
    def test_guards_internally_provider_bypasses_new_gates(self):
        p = _GuardsInternallyProvider()
        inst = self._inst(p)
        # seed care AR bloca daily-limit daca s-ar aplica -> nu trebuie sa blocheze
        for _ in range(60):
            p.seed_trade("BUY", age_sec=4000.0)
        order = inst.place("BUY", 100.0, 1.0)
        self.assertIsNotNone(order)
        self.assertEqual(len(p.placed), 1)
        # niciun log FLEET-WIDE nou (Binance isi loga singur, ca sa nu duplice)
        self.assertEqual(self._log_lines(), [])
        # a doua plasare imediata NU e blocata de cooldown (guards_internally sare peste el)
        order2 = inst.place("SELL", 101.0, 1.0)
        self.assertIsNotNone(order2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

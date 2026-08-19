"""kraken/replay.py — motorul de backtest care ruleaza STRATEGIA LIVE peste OHLC.
Caracterizare: pe o serie determinista, motorul ruleaza fara retea/notificari/fisiere
de stare si intoarce metrici sanatoase (contabilitate din _apply_fill-ul live)."""
import os
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# T212, Kraken and Hyperliquid expose generic modules named `strategy`,
# `market_data` and `notify`. Build the replay's Kraken import graph in isolation
# so full-suite collection order cannot substitute another venue's strategy.
_COLLIDING_MODULES = ("strategy", "market_data", "notify")
_PRELOADED_MODULES = {
    name: sys.modules.pop(name) for name in _COLLIDING_MODULES if name in sys.modules
}
try:
    _SPEC = importlib.util.spec_from_file_location(
        "kraken_replay_under_test", os.path.join(ROOT, "kraken", "replay.py")
    )
    rp = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = rp
    _SPEC.loader.exec_module(rp)
    strat = rp._strat
finally:
    for _name in _COLLIDING_MODULES:
        sys.modules.pop(_name, None)
    sys.modules.update(_PRELOADED_MODULES)


def _series():
    pts = []; p = 100.0
    for _ in range(8): p *= 0.985; pts.append((p, p*1.002, p*0.99, p*0.995))
    for _ in range(6): p *= 1.02;  pts.append((p, p*1.01, p*0.998, p*1.005))
    for _ in range(5): p *= 0.95;  pts.append((p, p*1.002, p*0.97, p*0.98))
    for _ in range(8): p *= 1.03;  pts.append((p, p*1.015, p*0.995, p*1.01))
    return pts


def _params(**over):
    d = dict(currency="USD", entry_amount=100.0, entry_discount_pct=0.2, dca_amount=50.0,
             dca_drop_pct=2.0, check_minutes=2.0, takeprofit_pct=3.0, max_budget=500.0,
             max_dca_buys=3, enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=12.5,
             adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=0.0, reentry_tolerance_pct=0.05,
             reentry_adaptive=False, reentry_sl_bounce_pct=1.5, tp_tranches=[])
    d.update(over)
    return strat.StratParams(**d)


class ReplayEngineTest(unittest.TestCase):
    def test_runs_and_returns_metrics(self):
        res = rp.run_replay(_series(), _params(), fee_pct=0.26, bar_minutes=60)
        for k in ("realized", "net", "fees", "total", "cycles", "wins", "maxdd",
                  "open_qty", "return_pct", "max_drawdown_pct", "sharpe", "sortino",
                  "calmar", "cvar_95_pct", "exposure_pct", "profit_factor",
                  "expectancy", "turnover_pct", "fills"):
            self.assertIn(k, res)
        self.assertEqual(res["cycles"], 2)          # motorul inchide 2 cicluri pe seria asta
        self.assertEqual(res["wins"], 1)
        self.assertEqual(res["open_qty"], 0.0)      # pozitie inchisa la final
        self.assertLess(res["net"], res["realized"]) # net = brut - fee-uri
        self.assertGreaterEqual(res["fees"], 0.0)
        self.assertGreaterEqual(res["maxdd"], 0.0)
        self.assertAlmostEqual(res["net_pnl"], res["total"])

    def test_no_state_file_written(self):
        before = set(os.listdir(os.path.join(ROOT, "kraken")))
        rp.run_replay(_series(), _params(), fee_pct=0.26)
        after = set(os.listdir(os.path.join(ROOT, "kraken")))
        new_state = [f for f in (after - before) if "REPLAY" in f or f.startswith(".state")]
        self.assertEqual(new_state, [], f"replay NU trebuie sa scrie stare: {new_state}")

    def test_existing_replay_state_cannot_contaminate_result(self):
        clean = rp.run_replay(_series(), _params(), fee_pct=0.26)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, ".state_REPLAY.json")
            contaminated = strat._new_state()
            contaminated.update({"qty": 99.0, "cost": 1.0, "realized_net": 999999.0})
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(contaminated, handle)
            with patch.object(strat, "state_path_for", return_value=state_path):
                replayed = rp.run_replay(_series(), _params(), fee_pct=0.26)
        self.assertEqual(replayed, clean)

    def test_order_created_at_close_cannot_fill_on_same_bar(self):
        # Low-ul 1 ar umple orice BUY, dar ordinul este decis abia la close=100.
        # Harness-ul trebuie să îl poată executa doar în bara următoare.
        first_bar = (100.0, 200.0, 1.0, 100.0)
        one_bar = rp.run_replay([first_bar], _params(), fee_pct=0.26)
        self.assertEqual(one_bar["fills"], 0)
        self.assertEqual(one_bar["open_qty"], 0.0)

        second_bar = (100.0, 101.0, 99.0, 100.0)
        two_bars = rp.run_replay([first_bar, second_bar], _params(), fee_pct=0.26)
        self.assertEqual(two_bars["fills"], 1)
        self.assertGreater(two_bars["open_qty"], 0.0)

    def test_market_stop_fills_at_next_open_even_below_reference_price(self):
        bars = [
            (100.0, 101.0, 99.0, 100.0),  # plasează intrarea
            (100.0, 101.0, 99.0, 100.0),  # umple intrarea
            (80.0, 82.0, 78.0, 80.0),     # declanșează STOP MARKET
            (70.0, 71.0, 69.0, 70.0),     # gap down: fill la open, nu așteaptă limita
        ]

        result = rp.run_replay(
            bars, _params(stop_loss_pct=10.0), fee_pct=0.26, bar_minutes=60,
        )

        self.assertEqual(result["fills"], 2)
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["open_qty"], 0.0)
        self.assertLess(result["net"], 0.0)

    def test_overlay_requires_replay_bars_at_configured_trend_interval(self):
        with self.assertRaisesRegex(ValueError, "trend_interval"):
            rp.run_replay(
                _series(), _params(trend_overlay=True, trend_interval=240),
                fee_pct=0.26, bar_minutes=60,
            )

    def test_adaptive_reentry_requires_bar_interval_for_time_scaling(self):
        with self.assertRaisesRegex(ValueError, "reentry_adaptive"):
            rp.run_replay(_series(), _params(reentry_adaptive=True), fee_pct=0.26)


if __name__ == "__main__":
    unittest.main(verbosity=2)

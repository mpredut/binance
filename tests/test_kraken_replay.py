"""kraken/replay.py — motorul de backtest care ruleaza STRATEGIA LIVE peste OHLC.
Caracterizare: pe o serie determinista, motorul ruleaza fara retea/notificari/fisiere
de stare si intoarce metrici sanatoase (contabilitate din _apply_fill-ul live)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import replay as rp
import strategy as strat


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
        res = rp.run_replay(_series(), _params(), fee_pct=0.26)
        for k in ("realized", "net", "fees", "total", "cycles", "wins", "maxdd", "open_qty"):
            self.assertIn(k, res)
        self.assertEqual(res["cycles"], 2)          # motorul inchide 2 cicluri pe seria asta
        self.assertEqual(res["wins"], 1)
        self.assertEqual(res["open_qty"], 0.0)      # pozitie inchisa la final
        self.assertLess(res["net"], res["realized"]) # net = brut - fee-uri
        self.assertGreaterEqual(res["fees"], 0.0)
        self.assertGreaterEqual(res["maxdd"], 0.0)

    def test_no_state_file_written(self):
        before = set(os.listdir(os.path.join(ROOT, "kraken")))
        rp.run_replay(_series(), _params(), fee_pct=0.26)
        after = set(os.listdir(os.path.join(ROOT, "kraken")))
        new_state = [f for f in (after - before) if "REPLAY" in f or f.startswith(".state")]
        self.assertEqual(new_state, [], f"replay NU trebuie sa scrie stare: {new_state}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

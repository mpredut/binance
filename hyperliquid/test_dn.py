#!/usr/bin/env python3
"""
Suita de teste pt edge-case-urile botului delta-neutral (autonomie pe server).
FARA API real, FARA bani: client fals, notificari capturate, stare in fisiere temp.

  /home/mariusp/binance/.venv/bin/python test_dn.py -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import delta_neutral as dn  # noqa: E402
from delta_neutral import DeltaNeutral, DNParams, state_path_for  # noqa: E402

COIN = "TSTDN"


def params(**over) -> DNParams:
    base = dict(coin=COIN, spot_pair="@999", spot_token=COIN, notional=100.0,
                entry_funding_hr=0.0, exit_funding_hr=-0.00005, funding_window_h=4.0,
                min_hold_h=6.0, rebalance_pct=5.0, check_minutes=5.0, sz_decimals=2,
                liq_alert_pct=20.0, auto_protect=True, reduce_pct=25.0, perp_leverage=1)
    base.update(over)
    return DNParams(**base)


class FakeClient:
    """Client fals: inregistreaza ordinele, simuleaza erori la cerere."""
    exchange = None

    def __init__(self):
        self.orders: list[tuple] = []
        self.fail_orders = False
        self.raise_account = False
        self.spot_bal = 2.0
        self.perp_szi = -2.0
        self.liq_px = 0.0

    def spot_mid(self, pair): return 50.0
    def mid(self, coin): return 50.0
    def funding_rate(self, coin): return 0.0000125

    def spot_balance_strict(self, token):
        if self.raise_account: raise RuntimeError("API down")
        return self.spot_bal

    def position_strict(self, coin):
        if self.raise_account: raise RuntimeError("API down")
        return self.perp_szi, 50.0

    def position_full(self, coin):
        return {"szi": self.perp_szi, "liquidationPx": self.liq_px}

    def spot_order(self, pair, is_buy, sz, px, szd):
        if self.fail_orders: return False, None, "err: margin"
        self.orders.append(("spot", "buy" if is_buy else "sell", sz))
        return True, 1, "ok"

    def place_limit(self, coin, is_buy, sz, px, reduce_only=False):
        if self.fail_orders: return False, None, "err: margin"
        self.orders.append(("perp", "buy" if is_buy else "sell", sz))
        return True, 2, "ok"

    def set_leverage(self, coin, lev): pass
    def open_orders(self, coin=None): return []
    def cancel(self, coin, oid): return True


def L(spot_qty=2.0, perp_szi=-2.0, funding=0.0000125):
    return {"spot_px": 50.0, "perp_px": 50.0, "funding": funding,
            "spot_qty": spot_qty, "perp_szi": perp_szi}


class Base(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.alerts: list[str] = []
        self._notify = dn.notify
        dn.notify = lambda **kw: self.alerts.append(kw.get("title", ""))
        self._cleanup_state()
        self.c = FakeClient()

    def tearDown(self):
        dn.notify = self._notify
        self._cleanup_state()
        os.environ.clear(); os.environ.update(self._env)

    @staticmethod
    def _cleanup_state():
        for suf in ("", ".lock", ".tmp"):
            p = state_path_for(COIN) + suf
            if os.path.exists(p):
                os.remove(p)

    def make(self, dry=False, **over) -> DeltaNeutral:
        d = DeltaNeutral(self.c, params(**over), dry_run=dry, desktop=False)
        return d

    def opened(self, d: DeltaNeutral, target=2.0):
        d.s["status"] = "open"; d.s["target_sz"] = target
        d.s["opened_ts"] = time.time()


# ---------------------------------------------------------------------------
class TestCitiriEsuate(Base):
    def test_eroare_api_la_cont_nu_ghiceste_si_nu_tranzactioneaza(self):
        d = self.make()
        self.c.raise_account = True
        self.assertIsNone(d.legs(), "citire esuata -> None, nu 0 fals")
        self.assertEqual(self.c.orders, [])

    def test_pret_lipsa_da_none(self):
        d = self.make()
        self.c.spot_mid = lambda pair: None
        self.assertIsNone(d.legs())


class TestPiciorOrfan(Base):
    def test_un_tick_suspect_nu_actioneaza(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=2.0, perp_szi=0.0))      # perp "disparut" — o singura citire
        self.assertEqual(self.c.orders, [], "anti-glitch: nu actioneaza din prima")
        self.assertEqual(d.s["orphan_count"], 1)

    def test_short_lichidat_inchide_spotul_dupa_confirmare(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=2.0, perp_szi=0.0))
        d.tick(L(spot_qty=2.0, perp_szi=0.0))      # confirmat
        sells = [o for o in self.c.orders if o[0] == "spot" and o[1] == "sell"]
        self.assertEqual(len(sells), 1, "vinde spotul ramas (de-risk)")
        self.assertEqual(d.s["status"], "flat")
        self.assertGreater(d.s["cooldown_until"], time.time(), "cooldown anti-thrash")
        self.assertTrue(any("picior disparut" in a for a in self.alerts))

    def test_glitch_recuperat_reseteaza_contorul(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=2.0, perp_szi=0.0))      # citire gresita o data
        d.tick(L())                                 # revine normal
        self.assertEqual(d.s["orphan_count"], 0)
        self.assertEqual(d.s["status"], "open")

    def test_ambele_disparute_trece_flat_fara_ordine(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=0.0, perp_szi=0.0))
        d.tick(L(spot_qty=0.0, perp_szi=0.0))
        self.assertEqual(self.c.orders, [], "nu are ce inchide")
        self.assertEqual(d.s["status"], "flat")

    def test_fara_auto_protect_doar_alerta(self):
        d = self.make(auto_protect=False); self.opened(d)
        d.tick(L(spot_qty=2.0, perp_szi=0.0))
        d.tick(L(spot_qty=2.0, perp_szi=0.0))
        self.assertEqual(self.c.orders, [])
        self.assertTrue(any("INTERVENTIE MANUALA" in a for a in self.alerts))


class TestDriftSiDust(Base):
    def test_drift_mare_cere_confirmare_2_tickuri(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=0.8, perp_szi=-2.0))     # spotul la 40% din tinta (>50% drift)
        self.assertEqual(self.c.orders, [], "primul tick: doar observa")
        d.tick(L(spot_qty=0.8, perp_szi=-2.0))     # confirmat -> corecteaza
        buys = [o for o in self.c.orders if o[0] == "spot" and o[1] == "buy"]
        self.assertEqual(len(buys), 1)

    def test_drift_mic_corecteaza_imediat(self):
        d = self.make(); self.opened(d)
        d.tick(L(spot_qty=1.6, perp_szi=-2.0))     # -20% drift: peste toleranta, sub 50%, peste $10
        buys = [o for o in self.c.orders if o[0] == "spot" and o[1] == "buy"]
        self.assertEqual(len(buys), 1, "driftul normal se corecteaza fara intarziere")

    def test_dust_sub_minim_nu_trimite_ordin(self):
        d = self.make(); self.opened(d, target=2.0)
        d._buy_spot(0.1, 50.0)                     # $5 < $10.5
        self.assertEqual(self.c.orders, [])


class TestOrdineEsuate(Base):
    def test_alerta_dupa_3_esecuri_consecutive(self):
        d = self.make(); self.opened(d)
        self.c.fail_orders = True
        for _ in range(3):
            d._buy_spot(1.0, 50.0)
        self.assertTrue(any("3 ordine esuate" in a for a in self.alerts))
        self.c.fail_orders = False
        d._buy_spot(1.0, 50.0)
        self.assertEqual(d.s["order_fails"], 0, "succesul reseteaza contorul")


class TestCooldownSiIntrare(Base):
    def test_cooldown_blocheaza_redeschiderea(self):
        d = self.make()
        d.s["cooldown_until"] = time.time() + 600
        d.tick(L(spot_qty=0.0, perp_szi=0.0, funding=0.001))   # funding excelent
        self.assertEqual(d.s["status"], "flat")
        self.assertEqual(self.c.orders, [])

    def test_dupa_cooldown_deschide(self):
        d = self.make()
        d.s["cooldown_until"] = time.time() - 1
        d.tick(L(spot_qty=0.0, perp_szi=0.0, funding=0.001))
        self.assertEqual(d.s["status"], "open")
        self.assertEqual(len(self.c.orders), 2, "ambele picioare plasate")


class TestIesireInteligenta(Base):
    def _force_avg(self, d, val):
        d.s["funding_hist"] = [[time.time(), val]] * 5

    def test_funding_negativ_dar_tinut_putin_nu_inchide(self):
        d = self.make(); self.opened(d)
        self._force_avg(d, -0.0002)
        d.tick(L(funding=-0.0002))
        self.assertEqual(d.s["status"], "open", "min_hold inca neimplinit")

    def test_funding_negativ_si_tinut_destul_inchide(self):
        d = self.make(); self.opened(d)
        d.s["opened_ts"] = time.time() - 7 * 3600   # tinut 7h > 6h
        self._force_avg(d, -0.0002)
        d.tick(L(funding=-0.0002))
        self.assertEqual(d.s["status"], "flat")
        self.assertEqual(len(self.c.orders), 2, "vinde spot + acopera perp")


class TestInfrastructura(Base):
    def test_a_doua_instanta_refuzata_de_lacat(self):
        d1 = self.make()
        self.assertTrue(d1._acquire_lock())
        d2 = self.make()
        self.assertFalse(d2._acquire_lock(), "lacatul previne dublarea ordinelor")
        d1._lock_fh.close()
        d3 = self.make()
        self.assertTrue(d3._acquire_lock(), "dupa oprire lacatul se elibereaza")
        d3._lock_fh.close()

    def test_salvarea_e_atomica_si_valida(self):
        d = self.make(); self.opened(d)
        d._save()
        with open(d.state_file) as f:
            st = json.load(f)
        self.assertEqual(st["status"], "open")
        self.assertFalse(os.path.exists(d.state_file + ".tmp"))

    def test_adopta_pozitia_existenta_la_restart(self):
        d = self.make()                              # stare proaspata (flat)
        d.tick(L(spot_qty=1.7, perp_szi=-1.71))
        self.assertEqual(d.s["status"], "open", "adopta in loc sa deschida dublu")
        self.assertAlmostEqual(d.s["target_sz"], 1.705, places=3)
        self.assertEqual(self.c.orders, [], "adoptarea nu plaseaza ordine noi")


class TestProtectieLichidare(Base):
    def test_auto_protect_reduce_ambele_picioare(self):
        d = self.make(); self.opened(d)
        self.c.liq_px = 55.0                         # pret 50, lichidare 55 -> 10% < 20%
        d.tick(L())
        covers = [o for o in self.c.orders if o[0] == "perp" and o[1] == "buy"]
        sells = [o for o in self.c.orders if o[0] == "spot" and o[1] == "sell"]
        self.assertEqual((len(covers), len(sells)), (1, 1), "reduce ambele picioare")
        self.assertTrue(any("LICHIDARE" in a or "redus" in a for a in self.alerts))


if __name__ == "__main__":
    unittest.main(verbosity=2)

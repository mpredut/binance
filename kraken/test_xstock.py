#!/usr/bin/env python3
"""
Suita de teste pt watcher-ul xStock + modul de adoptare din strategie.
FARA API real, FARA bani: client fals, notificari capturate, stare in fisiere temp.

  python3 test_xstock.py -v
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xstock_watch as xw  # noqa: E402
import strategy            # noqa: E402
from strategy import Strategy, StratParams, state_path_for  # noqa: E402


class FakeKraken:
    def __init__(self, bal=None, pairs=None, price=200.0):
        self.bal = bal or {}
        self.pairs = pairs or {}
        self.price = price

    def balance(self):
        return self.bal

    def asset_pairs(self):
        return self.pairs

    def last_price(self, pair):
        return self.price

    def pair_info(self, pair):
        return {"base": "TSTX", "pair_decimals": 2, "lot_decimals": 6, "ordermin": "0.01"}


class Base(unittest.TestCase):
    """Izolare completa: env restaurat, stare in temp, notify capturat."""

    def setUp(self):
        self._env = dict(os.environ)
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        self.state_path = path
        os.environ["XSTOCK_STATE_FILE"] = path
        self.alerts: list[str] = []
        self._xw_notify = xw.notify
        self._st_notify = strategy.notify
        xw.notify = lambda **kw: self.alerts.append(kw.get("title", ""))
        strategy.notify = lambda **kw: self.alerts.append(kw.get("title", ""))

    def tearDown(self):
        xw.notify = self._xw_notify
        strategy.notify = self._st_notify
        if os.path.exists(self.state_path):
            os.remove(self.state_path)
        os.environ.clear()
        os.environ.update(self._env)


# ---------------------------------------------------------------------------
# Watcher: persistenta si fara dubluri la restart
# ---------------------------------------------------------------------------
class TestWatcherPersistenta(Base):
    def test_alocare_o_singura_alerta_peste_restart(self):
        c = FakeKraken(bal={"ZUSD": "100"})
        st = xw._load_state()
        xw.check_balance(c, st, "SPCX", False)              # prima rulare = snapshot
        self.assertEqual(len(self.alerts), 0)
        c.bal = {"ZUSD": "100", "SPCXX": "37.5"}            # soseste alocarea
        xw.check_balance(c, st, "SPCX", False)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(st["allocated"]["asset"], "SPCXX")
        xw._save_state(st)
        st = xw._load_state()                               # *** RESTART ***
        xw.check_balance(c, st, "SPCX", False)
        self.assertEqual(len(self.alerts), 1, "restart nu trebuie sa redubleze alerta")
        self.assertEqual(st["allocated"]["qty"], 37.5, "alocarea trebuie stiuta dupa restart")

    def test_activ_nou_nepotrivit_da_alerta_informativa(self):
        c = FakeKraken(bal={"ZUSD": "100"})
        st = xw._load_state()
        xw.check_balance(c, st, "SPCX", False)
        c.bal = {"ZUSD": "100", "RANDOMCOIN": "5"}
        xw.check_balance(c, st, "SPCX", False)
        self.assertEqual(len(self.alerts), 1)
        self.assertIsNone(st["allocated"], "activ nepotrivit nu e alocare")

    def test_pereche_prefera_valuta_de_cotare_si_nu_dubleaza(self):
        pairs = {"SPCXXEUR": {"wsname": "SPCXx/EUR", "base": "SPCXX"},
                 "SPCXXUSD": {"wsname": "SPCXx/USD", "base": "SPCXX"}}
        c = FakeKraken(pairs=pairs)
        st = xw._load_state()
        xw.check_pairs(c, st, "SPCX", False, quote="USD")
        self.assertEqual(st["pair"], "SPCXXUSD", "trebuie preferata cotarea USD")
        self.assertEqual(len(self.alerts), 1)
        xw._save_state(st)
        st = xw._load_state()                               # restart
        xw.check_pairs(c, st, "SPCX", False, quote="USD")
        self.assertEqual(len(self.alerts), 1, "alerta de listare nu se redubleaza")

    def test_alerte_nivel_tp_sl_o_singura_data(self):
        c = FakeKraken(price=250.0)
        st = xw._load_state()
        st["allocated"] = {"asset": "SPCXX", "qty": 37.5}
        st["pair"] = "SPCXXUSD"
        xw.check_levels(c, st, 200.0, 20.0, 15.0, "", False)   # +25% > +20%
        xw.check_levels(c, st, 200.0, 20.0, 15.0, "", False)   # repetat
        self.assertEqual(len(self.alerts), 1, "alerta TP o singura data")
        c.price = 160.0                                        # -20% < -15%
        xw.check_levels(c, st, 200.0, 20.0, 15.0, "", False)
        self.assertEqual(len(self.alerts), 2, "si alerta SL o singura data")


# ---------------------------------------------------------------------------
# Auto-start: idempotent, watchdog, conditii
# ---------------------------------------------------------------------------
class TestAutoStart(Base):
    def setUp(self):
        super().setUp()
        self._script, self._log = xw.BOT_SCRIPT, xw.BOT_LOG
        fd, self.stub = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("import time\ntime.sleep(120)\n")
        xw.BOT_SCRIPT = self.stub
        xw.BOT_LOG = self.stub + ".log"
        os.environ["XSTOCK_AUTOSTART"] = "true"
        self.st = {"allocated": {"asset": "SPCXX", "qty": 37.5}, "pair": "SPCXXUSD",
                   "bot_pid": None, "alerted_need_price": False}

    def tearDown(self):
        if self.st.get("bot_pid"):
            try:
                os.kill(int(self.st["bot_pid"]), signal.SIGKILL)
                time.sleep(0.2)
                xw._bot_alive(self.st["bot_pid"])
            except (OSError, ValueError, TypeError):
                pass
        for p in (self.stub, xw.BOT_LOG):
            if os.path.exists(p):
                os.remove(p)
        xw.BOT_SCRIPT, xw.BOT_LOG = self._script, self._log
        super().tearDown()

    def test_porneste_si_e_idempotent(self):
        xw.maybe_start_bot(self.st, 200.0, False)
        pid = self.st["bot_pid"]
        self.assertTrue(xw._bot_alive(pid))
        self.assertEqual(len(self.alerts), 1)
        xw.maybe_start_bot(self.st, 200.0, False)           # a doua chemare
        self.assertEqual(self.st["bot_pid"], pid, "nu porneste al doilea bot")
        self.assertEqual(len(self.alerts), 1)

    def test_watchdog_reporneste_botul_mort_chiar_zombie(self):
        xw.maybe_start_bot(self.st, 200.0, False)
        pid = self.st["bot_pid"]
        os.kill(pid, signal.SIGKILL)                        # devine zombie (copilul nostru)
        time.sleep(0.3)
        self.assertFalse(xw._bot_alive(pid), "zombie-ul trebuie secerat si declarat mort")
        xw.maybe_start_bot(self.st, 200.0, False)
        self.assertNotEqual(self.st["bot_pid"], pid)
        self.assertTrue(xw._bot_alive(self.st["bot_pid"]))
        self.assertIn("REPORNIT", self.alerts[-1])

    def test_nu_porneste_fara_pret_dar_cere_pretul_o_data(self):
        xw.maybe_start_bot(self.st, 0.0, False)
        xw.maybe_start_bot(self.st, 0.0, False)
        self.assertIsNone(self.st["bot_pid"])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("XSTOCK_ALLOC_PRICE", self.alerts[0])

    def test_autostart_dezactivat_nu_porneste(self):
        os.environ["XSTOCK_AUTOSTART"] = "false"
        xw.maybe_start_bot(self.st, 200.0, False)
        self.assertIsNone(self.st["bot_pid"])
        self.assertEqual(len(self.alerts), 0)

    def test_nu_porneste_fara_alocare_sau_pereche(self):
        xw.maybe_start_bot({"allocated": None, "pair": "X", "bot_pid": None,
                            "alerted_need_price": False}, 200.0, False)
        xw.maybe_start_bot({"allocated": {"asset": "A", "qty": 1}, "pair": None,
                            "bot_pid": None, "alerted_need_price": False}, 200.0, False)
        self.assertEqual(len(self.alerts), 0)

    def test_paper_flag_e_transmis_botului(self):
        os.environ["XSTOCK_BOT_PAPER"] = "true"
        with open(self.stub, "w") as f:                     # stub care isi scrie argv
            f.write("import sys,time\nopen(sys.argv[0]+'.argv','w').write(' '.join(sys.argv[1:]))\ntime.sleep(120)\n")
        xw.maybe_start_bot(self.st, 200.0, False)
        time.sleep(0.6)
        argv = open(self.stub + ".argv").read()
        os.remove(self.stub + ".argv")
        self.assertIn("--paper", argv)
        self.assertIn("--pair SPCXXUSD", argv)


# ---------------------------------------------------------------------------
# Strategie: adoptarea pozitiei
# ---------------------------------------------------------------------------
class TestAdoptare(Base):
    PAIR = "TSTXUSD"

    def setUp(self):
        super().setUp()
        os.environ["STRAT_ADOPT_COST"] = "200"
        sp = state_path_for(self.PAIR)
        if os.path.exists(sp):
            os.remove(sp)

    def tearDown(self):
        sp = state_path_for(self.PAIR)
        if os.path.exists(sp):
            os.remove(sp)
        super().tearDown()

    def _strategy(self, client):
        return Strategy(client, self.PAIR, StratParams.from_env(), dry_run=True, desktop=False)

    def test_adopta_din_balanta_si_pune_tp(self):
        s = self._strategy(FakeKraken(bal={"TSTX": "37.5"}))
        s.step(200.0)
        self.assertTrue(s.s["adopted"])
        # adoptarea din balanta lasa un praf (balanta Kraken e raportata rotunjit)
        self.assertAlmostEqual(s.s["qty"], 37.5, delta=0.001)
        self.assertLess(s.s["qty"], 37.5 + 1e-12, "nu vinde niciodata peste ledger")
        self.assertAlmostEqual(s.s["cost"] / s.s["qty"], 200.0)
        sells = [o for o in s.s["orders"] if o["side"] == "sell" and o["kind"] == "TP"]
        self.assertEqual(len(sells), 1, "TP plasat imediat dupa adoptare")

    def test_asteapta_alocarea_fara_sa_cumpere(self):
        c = FakeKraken(bal={"ZUSD": "100"})                 # inca fara alocare
        s = self._strategy(c)
        s.step(100.0)
        self.assertEqual(s.s["orders"], [], "NU cumpara intrare noua cat asteapta")
        c.bal = {"ZUSD": "100", "TSTX": "37.5"}             # soseste
        s.step(200.0)
        self.assertTrue(s.s["adopted"])

    def test_nu_adopta_peste_ciclu_in_curs(self):
        import json
        with open(state_path_for(self.PAIR), "w") as f:
            json.dump({"cycle": 3, "qty": 2.46, "cost": 150.0, "spent": 150.0,
                       "dca_buys": 1, "orders": []}, f)
        s = self._strategy(FakeKraken(bal={"TSTX": "99"}))
        s._maybe_adopt()
        self.assertFalse(s.s.get("adopted", False))
        self.assertEqual(s.s["qty"], 2.46, "pozitia existenta ramane neatinsa")

    def test_nu_readopta_dupa_restart(self):
        s = self._strategy(FakeKraken(bal={"TSTX": "37.5"}))
        s._maybe_adopt()
        q1 = s.s["qty"]
        s._save()
        s2 = self._strategy(FakeKraken(bal={"TSTX": "99999"}))   # balanta crescuta intre timp
        s2._maybe_adopt()
        self.assertEqual(s2.s["qty"], q1, "restart nu re-adopta / nu dubleaza")

    def test_alocarea_nu_consuma_plafonul_dca(self):
        s = self._strategy(FakeKraken(bal={"TSTX": "37.5"}))
        s._maybe_adopt()
        self.assertEqual(s.s["spent"], 0.0, "plafonul ramane pt bani aditionali")


if __name__ == "__main__":
    unittest.main(verbosity=2)

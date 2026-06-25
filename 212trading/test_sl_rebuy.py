#!/usr/bin/env python3
"""Verificare sl_rebuy (re-buy pe recul dupa stop-loss de catastrofa) pe calea REALA.

Reproduce scenariul live (dry_run=False, stub fara retea):
  1. detinem o pozitie, SL de catastrofa tocmai a vandut tot  -> ciclu inchis
  2. _reconcile_real vede portofoliu=0 cu sl_pending  -> ARMEAZA sl_rebuy
  3. pretul continua sa cada                          -> NU cumpara (urmareste fundul)
  4. recul < bounce_pct                               -> inca NU cumpara
  5. recul >= bounce_pct de la minim                  -> plaseaza BUY ENTRY (1 transa)
  6. armarea e consumata                              -> nu mai recumpara
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import strategy as strat_mod  # noqa: E402
from strategy import Strategy, StratParams, state_path_for  # noqa: E402

strat_mod.notify = lambda **kw: None   # fara telegram/desktop in test

TICK = "SLREBUYTEST_US_EQ"


class StubClient:
    """T212 client minimal: portofoliu + ordine controlabile din test."""
    def __init__(self):
        self.portfolio = [{"ticker": TICK, "quantity": 1.0, "averagePrice": 100.0}]
        self.active = []
        self.placed = []
        self._id = 0

    def get_portfolio(self):
        return self.portfolio

    def list_active_orders(self):
        return self.active

    def cancel_order(self, oid):
        self.active = [o for o in self.active if o.get("id") != oid]

    def place_limit_order(self, ticker, qty, limit, validity):
        self._id += 1
        oid = f"O{self._id}"
        o = {"id": oid, "ticker": ticker, "quantity": qty, "limit": limit}
        self.placed.append(o)
        self.active.append(o)
        return 201, {"id": oid}


def main() -> int:
    # stare curata
    sf = state_path_for(TICK)
    if os.path.exists(sf):
        os.remove(sf)

    params = StratParams.from_env({
        "STRAT_CURRENCY": "USD",          # 1:1 -> fara retea FX
        "YAHOO_SYMBOL": "SLREBUY",
        "STRAT_ENTRY": "100",
        "STRAT_MAX_BUDGET": "1000",
        "STRAT_ENTRY_DISCOUNT_PCT": "0.2",
        "STRAT_STOP_LOSS_PCT": "30",
        "STRAT_SL_REBUY_ENABLED": "true",
        "STRAT_SL_REBUY_BOUNCE_PCT": "1.2",
        "STRAT_REENTRY_DROP_PCT": "1.0",  # garda profit: ar bloca reintrarea peste last_sell daca sl_rebuy n-ar avea prioritate
    })
    client = StubClient()
    s = Strategy(client, TICK, params, dry_run=False)

    fails = []

    def check(cond, msg):
        print(("  OK  " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # --- starea de plecare: detinem 1.0 @ 100, SL tocmai a fost plasat (catastrofa) ---
    s.s["qty"] = 1.0
    s.s["cost_usd"] = 100.0
    s.s["entry_price"] = 100.0
    s.s["last_buy_price"] = 100.0
    s.s["spent_cash"] = 100.0
    s.s["sl_pending"] = True            # marcat de _check_stop_loss inainte ca SL-ul sa execute

    # 1+2. SL-ul a executat -> portofoliu 0; reconcilierea vede inchiderea de catastrofa
    client.portfolio = [{"ticker": TICK, "quantity": 0.0, "averagePrice": 0.0}]
    client.active = []
    s._reconcile_real(94.0)
    rb = s.s.get("sl_rebuy")
    check(rb is not None, "sl_rebuy ARMAT dupa inchiderea din stop-loss")
    check(abs(s.s.get("last_sell_price", 0) - 94.0) < 1e-9, "last_sell_price = pret vanzare (94)")
    check(s.s["qty"] <= 1e-9, "pozitie inchisa (qty 0) dupa SL")
    n_after_arm = len(client.placed)

    # 3. pretul cade mai adanc -> urmareste fundul, NU cumpara
    s.step(92.0)
    check(s.s.get("sl_rebuy") is not None, "inca armat in caderea adanca (92)")
    check(abs(s.s["sl_rebuy"]["low"] - 92.0) < 1e-9, "minimul urmarit cobora la 92")
    check(len(client.placed) == n_after_arm, "NICIUN buy in caderea adanca (nu prinde cutitul)")

    # 4. recul mic (+0.5% de la 92) < prag 1.2% -> inca nu cumpara
    s.step(92.0 * 1.005)
    check(len(client.placed) == n_after_arm, "recul +0.5% < 1.2% -> inca fara buy")
    check(s.s.get("sl_rebuy") is not None, "inca armat dupa recul insuficient")

    # 5. recul >= 1.2% de la minimul 92 -> plaseaza BUY ENTRY
    s.step(92.0 * 1.013)
    check(len(client.placed) == n_after_arm + 1, "recul +1.3% >= 1.2% -> BUY ENTRY plasat")
    if len(client.placed) == n_after_arm + 1:
        last = client.placed[-1]
        check(last["quantity"] > 0, "ordinul plasat e BUY (qty>0)")

    # 6. armarea consumata -> nu mai recumpara la urmatorul recul
    s.step(92.0 * 1.02)
    check(s.s.get("sl_rebuy") is None, "armarea CONSUMATA (1 transa) dupa re-buy")
    check(len(client.placed) == n_after_arm + 1, "fara al doilea buy (armare consumata)")

    if os.path.exists(sf):
        os.remove(sf)

    print()
    if fails:
        print(f"=== {len(fails)} VERIFICARI ESUATE ===")
        return 1
    print("=== TOATE VERIFICARILE AU TRECUT ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

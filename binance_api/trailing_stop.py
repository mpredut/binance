#!/usr/bin/env python3
"""
trailing_stop.py — TRAILING STOP per-moneda pe holdings Binance.

De ce exista: assetguardian.sell_all_assets() declanseaza corect dar cheama
place_safe_order(force=False) -> trece prin apply_weight_limit -> intr-un uptrend
ponderea contra-trend e 0.02 -> ordinul e zero-uit -> "Orders sent: 0". Adica
mecanismul de protectie nu vinde NIMIC (vezi logul TAO 13 iun: a tras de sute de
ori, a vandut 0). Trailing stop-ul:
  * tine pozitia cat pretul URCA (urmareste varful)
  * vinde DOAR cand pretul scade trail% de la varf -> protejeaza castigul real
  * foloseste force=True -> ocoleste weight-ul (altfel ar fi zero-uit la fel)

ONEST (walk-forward out-of-sample, feed real 291z): trailing-ul STRANS NU bate
detinerea — declinul vine cu reculuri care produc whipsaw + fee. NU e o sursa de
profit. Rol corect: DISJUNCTOR DE CRASH cu prag LARG (~22%) — se declanseaza doar
la un colaps sustinut, ca plasa impotriva scenariului care distruge detinerea.
Ruleaza-l in dry-run intai; restul strategiei (hold+DCA+weight) ramane neschimbat.

SIGURANTA:
  * TRAILING_ENABLED=false (implicit) -> DRY-RUN: doar logheaza ce AR vinde.
  * actioneaza doar pe monedele din symbols.py.
  * varful e persistat (supravietuieste restartului) -> nu se reseteaza.
  * sare ordinele sub notional minim.

  TRAILING_ENABLED=true python trailing_stop.py            # bucla
  python trailing_stop.py --once                            # o verificare (dry-run)
  python trailing_stop.py --status                          # varfuri + praguri curente
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # binance_api/ -> radacina repo
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)   # ruleaza si ca script (python binance_api/trailing_stop.py)
DEFAULT_STATE = os.path.join(_ROOT, "cachedb", "trailing_state.json")

# PRAG LARG = DISJUNCTOR DE CRASH, nu unealta de profit.
# Walk-forward out-of-sample (feed real 291z) a aratat ca trailing-ul STRANS (8-12%)
# NU bate detinerea — declinul vine cu reculuri violente care produc whipsaw + fee.
# Singura valoare reala e protectia impotriva unui COLAPS sustinut (fara reculuri):
# prag larg (~22%) se declanseaza doar la o cadere catastrofala, nu pe zgomot.
TRAIL_PCT = {
    "BTCUSDC": 20.0,
    "TAOUSDC": 22.0,
}
DEFAULT_TRAIL_PCT = 22.0
SELL_FRACTION = float(os.environ.get("TRAILING_SELL_FRACTION", "1.0"))  # 1.0=tot, 0.5=jumatate
MIN_NOTIONAL_USD = 11.0
CHECK_SECONDS = float(os.environ.get("TRAILING_CHECK_SECONDS", "60"))

# RE-BUY dupa stop-loss de crash: trailing-ul a vandut (cu bypass) -> tot el recumpara (cu bypass),
# scutit de garda de profit (care, cu fereastra ei lunga, ar bloca re-intrarea). Declansare: pretul
# revine REBUY_BOUNCE_PCT% de la minimul de dupa vanzare (confirmare ca s-a oprit caderea) -> nu
# prinde cutitul. 1 transa acum; REBUY_TRANCHES rezervat pt extindere (DCA pe dip).
REBUY_ENABLED = os.environ.get("TRAILING_REBUY_ENABLED", "true").lower() == "true"
REBUY_BOUNCE_PCT = float(os.environ.get("TRAILING_REBUY_BOUNCE_PCT", "3.0"))
REBUY_TRANCHES = int(os.environ.get("TRAILING_REBUY_TRANCHES", "1"))


def should_sell(current: float, peak: float, trail_pct: float) -> bool:
    """True daca pretul a cazut >= trail% de la varf."""
    return peak > 0 and trail_pct > 0 and current <= peak * (1 - trail_pct / 100.0)


class TrailingStop:
    def __init__(self, api, po, sym, log=print, enabled=None,
                 sell_fraction=SELL_FRACTION, state_file=DEFAULT_STATE):
        self.api = api
        self.po = po
        self.sym = sym
        self.log = log
        self.enabled = (os.environ.get("TRAILING_ENABLED", "false").lower() == "true"
                        if enabled is None else enabled)
        self.sell_fraction = sell_fraction
        self.state_file = state_file

    # -- stare (varful per moneda) --------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            self.log(f"  ! [TRAIL] nu pot salva starea: {e}")

    def trail_pct_for(self, symbol: str) -> float:
        return TRAIL_PCT.get(symbol, DEFAULT_TRAIL_PCT)

    def _free_qty(self, balances: list, asset: str) -> float:
        for bal in balances or []:
            if bal.get("asset") == asset:
                try:
                    return float(bal.get("free", 0.0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    # -- re-buy dupa crash sell ------------------------------------------------
    def _handle_rebuy(self, symbol: str, st: dict, price: float) -> None:
        """Recumparare dupa stop-loss de crash: cand pretul revine REBUY_BOUNCE_PCT% de la minimul
        de dupa vanzare (confirma ca s-a oprit caderea), recumpara qty vanduta (force + bypass garda)."""
        rb = st.get("rebuy")
        if not rb:
            return
        rb["low"] = min(rb.get("low", price), price)          # urmareste fundul de dupa vanzare
        if price < rb["low"] * (1 + REBUY_BOUNCE_PCT / 100.0):
            return                                            # reculul inca neconfirmat -> asteapta
        buy_qty = round(float(rb.get("qty", 0)), 8)           # 1 transa = qty intreg (extensibil la REBUY_TRANCHES)
        if buy_qty <= 0:
            st.pop("rebuy", None)
            return
        if self.enabled and buy_qty * price >= MIN_NOTIONAL_USD:
            self.po.place_safe_order("BUY", symbol, price, buy_qty, force=True, bypass_profit_guard=True)
            self.log(f"  🟢 [TRAIL] RE-BUY {symbol} {buy_qty} @ ~{price:.4f}  "
                     f"(recul +{REBUY_BOUNCE_PCT}% de la minim {rb['low']:.4f}; vandut la {rb.get('sell_price', 0):.4f})")
        else:
            self.log(f"  🟡 [TRAIL][DRY] AR RE-CUMPARA {symbol} {buy_qty} @ ~{price:.4f}  "
                     f"(recul de la minim {rb['low']:.4f})  [TRAILING_ENABLED=true ca sa execute]")
        st.pop("rebuy", None)                                 # 1 transa -> gata

    # -- un pas ----------------------------------------------------------------
    def check_once(self) -> None:
        try:
            balances = self.api.get_account_assets_balances()
        except Exception as e:  # noqa: BLE001
            self.log(f"  ! [TRAIL] balante indisponibile ({e}) — sar tick-ul")
            return
        state = self._load()
        for symbol in self.sym.symbols:
            try:
                asset = self.api.split_symbol(symbol)[0]
                qty = self._free_qty(balances, asset)
                price = self.api.get_current_price(symbol)
                if not price or price <= 0:
                    continue
                st = state.setdefault(symbol, {"peak": price})
                if REBUY_ENABLED and st.get("rebuy"):       # re-buy pending dupa crash — INAINTE de check-ul de qty (qty~0 dupa vanzare)
                    self._handle_rebuy(symbol, st, price)
                if qty * price < MIN_NOTIONAL_USD:
                    continue                                # nimic de protejat
                if price > st["peak"]:
                    st["peak"] = price                      # varf nou -> urca trailing-ul
                trail = self.trail_pct_for(symbol)
                stop_at = st["peak"] * (1 - trail / 100.0)
                if should_sell(price, st["peak"], trail):
                    sell_qty = round(qty * self.sell_fraction, 8)
                    if self.enabled and sell_qty * price >= MIN_NOTIONAL_USD:
                        # force=True -> vinde la MARKET (executie sigura in crash);
                        # bypass_profit_guard=True -> ignora gardul de profit/istorie (e STOP-LOSS,
                        # vinde sub ultimul buy). Fara bypass, gardul l-ar bloca.
                        self.po.place_safe_order("SELL", symbol, price, sell_qty, force=True, bypass_profit_guard=True)
                        self.log(f"  🛑 [TRAIL] VANDUT {symbol} {sell_qty} @ ~{price:.4f} "
                                 f"(varf {st['peak']:.4f}, -{trail}%)")
                        st["peak"] = price                  # re-armeaza de la pretul curent
                        if REBUY_ENABLED:                   # armeaza re-buy: recumpara cand pretul revine de la minim
                            st["rebuy"] = {"qty": sell_qty, "sell_price": price, "low": price}
                    else:
                        self.log(f"  🟡 [TRAIL][DRY] AR VINDE {symbol} {sell_qty} @ ~{price:.4f} "
                                 f"(varf {st['peak']:.4f}, scadere >= {trail}%)  "
                                 f"[seteaza TRAILING_ENABLED=true ca sa execute]")
                else:
                    self.log(f"  [TRAIL] {symbol}: {price:.4f}  varf {st['peak']:.4f}  "
                             f"vinde sub {stop_at:.4f} (-{trail}%)")
            except Exception as e:  # noqa: BLE001 — o moneda nu opreste restul
                self.log(f"  ! [TRAIL] {symbol}: {e}")
        self._save(state)

    def run(self):
        mode = "⚠ ACTIV (vinde real)" if self.enabled else "DRY-RUN (doar logheaza)"
        self.log(f"=== TRAILING STOP pornit — {mode} ===")
        self.log(f"    monede/praguri: " +
                 ", ".join(f"{s}={self.trail_pct_for(s)}%" for s in self.sym.symbols))
        while True:
            try:
                self.check_once()
            except KeyboardInterrupt:
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"  ! [TRAIL] eroare ciclu ({e}) — continui")
            time.sleep(CHECK_SECONDS)


def main() -> int:
    ap = argparse.ArgumentParser(description="Trailing stop per-moneda (Binance).")
    ap.add_argument("--once", action="store_true", help="o verificare si iese")
    ap.add_argument("--status", action="store_true", help="varfuri + praguri curente")
    args = ap.parse_args()

    from binance_api import bapi as api
    from binance_api import bapi_placeorder as po
    import symbols as sym
    ts = TrailingStop(api, po, sym)

    if args.status:
        state = ts._load()
        for s in sym.symbols:
            st = state.get(s, {})
            tr = ts.trail_pct_for(s)
            peak = st.get("peak")
            print(f"{s}: varf={peak}  trailing={tr}%  "
                  f"vinde sub {peak * (1 - tr / 100):.4f}" if peak else f"{s}: fara varf inca")
        print(f"ENABLED={ts.enabled} (dry-run daca False)")
        return 0
    if args.once:
        ts.check_once()
        return 0
    ts.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

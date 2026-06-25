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

Masina de stari (trailing + re-buy) e in trailing_core.TrailingCore, partajata cu
Kraken; aici e doar ADAPTORUL Binance (API + log specific). Comportament identic —
vezi tests/test_trailing_stop.py.

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
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # binance_api/ -> radacina repo
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)   # ruleaza si ca script (python binance_api/trailing_stop.py)

from trailing_core import TrailingCore, should_sell  # noqa: E402  (should_sell reexportat pt teste/compat)

DEFAULT_STATE = os.path.join(_ROOT, "cachedb", "trailing_state.json")


def _load_conf():
    """Incarca trailing.conf (KEY=VALUE) si populeaza env vars (daca nu sunt deja setate extern)."""
    conf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trailing.conf")
    try:
        with open(conf_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    if k and k not in os.environ:   # env extern suprascrie (pt test ad-hoc)
                        os.environ[k] = v.strip()
    except OSError:
        pass   # lipsa fisier -> valorile din env/default raman


_load_conf()

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
REBUY_BOUNCE_PCT = float(os.environ.get("TRAILING_REBUY_BOUNCE_PCT", "1.2"))
REBUY_TRANCHES = int(os.environ.get("TRAILING_REBUY_TRANCHES", "1"))
# Filtre de trend (citite din cache_instant_trend via cacheManager). ACTIONEAZA doar la semnal
# CLAR opus; neutru/necunoscut -> NU blocheaza (degradare sigura, comportament = ca fara filtru).
# Re-buy: sari daca trend CLAR jos (nu prinde cutitul). Sell de crash: implicit NEFILTRAT (disjunctorul
# trebuie sa ramana fiabil); pune true daca vrei sa NU vinda cand trendul instant e clar SUS (anti-wick).
REBUY_SKIP_IF_TREND_DOWN = os.environ.get("TRAILING_REBUY_SKIP_IF_TREND_DOWN", "true").lower() == "true"
SELL_SKIP_IF_TREND_UP = os.environ.get("TRAILING_SELL_SKIP_IF_TREND_UP", "false").lower() == "true"
# Prag minim de profit inainte sa se activeze trailing-ul (0 = activ imediat, ca inainte).
# Previne vanzarea in pierdere dupa un dip normal imediat dupa cumparare.
MIN_PROFIT_PCT = float(os.environ.get("TRAILING_MIN_PROFIT_PCT", "0.0"))


class TrailingStop:
    """ADAPTOR Binance pt TrailingCore: pune la dispozitie API-ul (balante, pret, sell/buy,
    trend) + log-urile specifice. Masina de stari (varf, trailing, re-buy) e in TrailingCore."""

    def __init__(self, api, po, sym, log=print, enabled=None,
                 sell_fraction=SELL_FRACTION, state_file=DEFAULT_STATE,
                 min_profit_pct=MIN_PROFIT_PCT):
        self.api = api
        self.po = po
        self.sym = sym
        self.log = log
        self.enabled = (os.environ.get("TRAILING_ENABLED", "false").lower() == "true"
                        if enabled is None else enabled)
        self.sell_fraction = sell_fraction
        self.state_file = state_file
        self._balances = []
        self.core = TrailingCore(
            self, log=log, enabled=self.enabled, state_file=state_file,
            min_notional=MIN_NOTIONAL_USD, rebuy_enabled=REBUY_ENABLED,
            rebuy_bounce_pct=REBUY_BOUNCE_PCT,
            rebuy_skip_if_trend_down=REBUY_SKIP_IF_TREND_DOWN,
            sell_skip_if_trend_up=SELL_SKIP_IF_TREND_UP,
            sell_fraction=sell_fraction, item_isolation=True,
            min_profit_pct=min_profit_pct)

    # -- stare (delegare la core; pastrate pt --status si teste) ---------------
    def _load(self) -> dict:
        return self.core.load()

    def _save(self, state: dict):
        self.core.save(state)

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

    # -- trend instant (din cacheManager) — pt filtrele optionale --------------
    def _trend_value(self, symbol: str) -> float:
        """Panta trendului instant (>0 sus, <0 jos, 0 neutru/necunoscut). 0 la orice eroare
        -> filtrele de trend devin no-op (degradare sigura, comportament ca fara filtru)."""
        try:
            import cacheManager as cm
            snap = cm.get_short_trend_manager().get_snapshot(symbol)
            if snap:
                return float(snap.get('gradient_recent', snap.get('slope_small', 0.0)) or 0.0)
        except Exception:
            pass
        return 0.0

    # == contract ADAPTOR pt TrailingCore =====================================
    def assets(self):
        for symbol in self.sym.symbols:
            asset = self.api.split_symbol(symbol)[0]
            yield (symbol, asset, symbol, self.trail_pct_for(symbol))  # key=pair=symbol pe Binance

    def begin_tick(self) -> bool:
        try:
            self._balances = self.api.get_account_assets_balances()
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"  ! [TRAIL] balante indisponibile ({e}) — sar tick-ul")
            return False

    def free_qty(self, asset: str) -> float:
        return self._free_qty(self._balances, asset)

    def price(self, pair: str):
        return self.api.get_current_price(pair)

    def trend(self, pair: str) -> float:
        return self._trend_value(pair)

    def execute_sell(self, key, asset, pair, qty, price, peak, trail) -> bool:
        # force=True -> vinde la MARKET (executie sigura in crash);
        # bypass_profit_guard=True -> ignora gardul de profit/istorie (e STOP-LOSS,
        # vinde sub ultimul buy). Fara bypass, gardul l-ar bloca.
        self.po.place_safe_order("SELL", pair, price, qty, force=True, bypass_profit_guard=True)
        self.log(f"  🛑 [TRAIL] VANDUT {pair} {qty} @ ~{price:.4f} "
                 f"(varf {peak:.4f}, -{trail}%)")
        return True

    def execute_rebuy(self, key, asset, pair, qty, price, rb) -> bool:
        self.po.place_safe_order("BUY", pair, price, qty, force=True, bypass_profit_guard=True)
        self.log(f"  🟢 [TRAIL] RE-BUY {pair} {qty} @ ~{price:.4f}  "
                 f"(recul +{REBUY_BOUNCE_PCT}% de la minim {rb['low']:.4f}; vandut la {rb.get('sell_price', 0):.4f})")
        return True

    def log_dry_sell(self, key, asset, pair, qty, price, peak, trail) -> None:
        self.log(f"  🟡 [TRAIL][DRY] AR VINDE {pair} {qty} @ ~{price:.4f} "
                 f"(varf {peak:.4f}, scadere >= {trail}%)  "
                 f"[seteaza TRAILING_ENABLED=true ca sa execute]")

    def log_dry_rebuy(self, key, asset, pair, qty, price, rb) -> None:
        self.log(f"  🟡 [TRAIL][DRY] AR RE-CUMPARA {pair} {qty} @ ~{price:.4f}  "
                 f"(recul de la minim {rb['low']:.4f})  [TRAILING_ENABLED=true ca sa execute]")

    def log_hold(self, key, asset, pair, price, peak, stop_at, trail, free) -> None:
        self.log(f"  [TRAIL] {pair}: {price:.4f}  varf {peak:.4f}  "
                 f"vinde sub {stop_at:.4f} (-{trail}%)")

    def log_skip_rebuy_trend(self, asset) -> None:
        self.log(f"  [TRAIL] re-buy {asset} amanat — trend instant CLAR jos (nu prind cutitul)")

    def log_skip_sell_trend(self, key, asset, pair, trail) -> None:
        self.log(f"  [TRAIL] {pair}: -{trail}% atins dar trend instant SUS — NU vand (anti-wick)")

    def log_item_error(self, key, e) -> None:
        self.log(f"  ! [TRAIL] {key}: {e}")

    # -- un pas / bucla --------------------------------------------------------
    def check_once(self) -> None:
        self.core.check_once()

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

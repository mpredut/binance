#!/usr/bin/env python3
"""
trailing_stop.py (Kraken) — DISJUNCTOR DE CRASH pe holdingurile manuale de pe Kraken.

Protejeaza HYPE-ul cumparat manual (~$1.7k) impotriva unui colaps sustinut, FARA
sa-ti capeze upside-ul (prag larg) si fara whipsaw (15%, nu 7%). Vinde DOAR
balanta LIBERA (free = total - hold_trade), deci NU atinge cei 3.38 HYPE blocati
in ordinul de TP al botului — separare curata.

ONEST (vezi walk-forward in conversatie): trailing-ul nu produce alfa; rolul lui
e protectia de crash. Prag larg ~15% = se declanseaza doar la o cadere reala.

RE-BUY dupa crash sell (ca pe Binance binance_api/trailing_stop.py): dupa ce vinde
in crash (force), tot el recumpara cand pretul revine REBUY_BOUNCE_PCT% de la minimul
de dupa vanzare (confirma ca s-a oprit caderea -> nu prinde cutitul). Filtre de trend
optionale (cache_instant_trend HYPE). Config in kraken/trailing.conf (nu in shell).

Masina de stari (trailing + re-buy) e in trailing_core.TrailingCore, partajata cu
Binance; aici e doar ADAPTORUL Kraken (API + log/notify specific). Comportament
identic — vezi kraken/test_trailing_kraken.py.

  python3 trailing_stop.py        # bucla (enabled din trailing.conf)
  python3 trailing_stop.py --once                              # o verificare
  python3 trailing_stop.py --status                            # varfuri + praguri
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from kraken_common import load_dotenv, log, float_env
from kraken_client import KrakenClient, KrakenError
from notify import notify

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)   # ca sa importam nucleul partajat din radacina repo

from trailing_core import TrailingCore, should_sell  # noqa: E402  (should_sell reexportat pt teste/compat)

STATE_FILE = os.path.join(_HERE, "trailing_state.json")
CACHE_TREND = os.path.join(_ROOT, "cachedb", "cache_instant_trend.json")


def _load_conf():
    """Incarca kraken/trailing.conf (KEY=VALUE) in env (daca nu-s setate extern).
    ENV extern suprascrie config-ul (pt test ad-hoc)."""
    conf = os.path.join(_HERE, "trailing.conf")
    try:
        with open(conf) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k and k not in os.environ:
                    os.environ[k] = v.strip()
    except OSError:
        pass


_load_conf()

# prag larg per ASSET (disjunctor de crash, nu unealta de profit) + perechea de vanzare
TRAIL_PCT = {"HYPE": 15.0}
PAIR_FOR = {"HYPE": "HYPEUSD"}
DEFAULT_TRAIL_PCT = 15.0
MIN_NOTIONAL_USD = 10.0
CHECK_SECONDS = float(os.environ.get("KRAKEN_TRAILING_CHECK_SECONDS", "120"))

# RE-BUY dupa crash sell: recumpara cand pretul revine BOUNCE% de la minimul de dupa vanzare.
REBUY_ENABLED = os.environ.get("KRAKEN_TRAILING_REBUY_ENABLED", "true").lower() == "true"
REBUY_BOUNCE_PCT = float(os.environ.get("KRAKEN_TRAILING_REBUY_BOUNCE_PCT", "1.2"))
# Filtre de trend (din cache_instant_trend HYPE). ACTIONEAZA doar la semnal CLAR opus;
# neutru/necunoscut -> NU blocheaza (degradare sigura). Re-buy: sari daca trend CLAR jos.
# Sell de crash: implicit NEFILTRAT (disjunctorul ramane fiabil); true = anti-wick.
REBUY_SKIP_IF_TREND_DOWN = os.environ.get("KRAKEN_TRAILING_REBUY_SKIP_IF_TREND_DOWN", "true").lower() == "true"
SELL_SKIP_IF_TREND_UP = os.environ.get("KRAKEN_TRAILING_SELL_SKIP_IF_TREND_UP", "false").lower() == "true"
# Prag minim de profit inainte sa se activeze trailing-ul (0 = activ imediat, ca inainte).
# Previne vanzarea in pierdere dupa un dip normal imediat dupa cumparare.
MIN_PROFIT_PCT = float(os.environ.get("KRAKEN_TRAILING_MIN_PROFIT_PCT", "0.0"))


class KrakenTrailing:
    """ADAPTOR Kraken pt TrailingCore: API (balanta libera, pret, sell/buy limit, trend) +
    log/notify specific. Masina de stari (varf, trailing, re-buy) e in TrailingCore."""

    def __init__(self, client: KrakenClient, log=log, enabled=None, state_file=STATE_FILE,
                 min_profit_pct=MIN_PROFIT_PCT):
        self.client = client
        self.log = log
        self.enabled = (os.environ.get("KRAKEN_TRAILING_ENABLED", "false").lower() == "true"
                        if enabled is None else enabled)
        self.state_file = state_file
        self.core = TrailingCore(
            self, log=log, enabled=self.enabled, state_file=state_file,
            min_notional=MIN_NOTIONAL_USD, rebuy_enabled=REBUY_ENABLED,
            rebuy_bounce_pct=REBUY_BOUNCE_PCT,
            rebuy_skip_if_trend_down=REBUY_SKIP_IF_TREND_DOWN,
            sell_skip_if_trend_up=SELL_SKIP_IF_TREND_UP,
            sell_fraction=1.0, item_isolation=False,
            min_profit_pct=min_profit_pct)

    # -- stare (delegare la core; pastrate pt --status si teste) ---------------
    def _load(self) -> dict:
        return self.core.load()

    def _save(self, st: dict):
        self.core.save(st)

    def trail_pct_for(self, asset: str) -> float:
        return TRAIL_PCT.get(asset, DEFAULT_TRAIL_PCT)

    def _free(self, asset: str) -> float:
        """Balanta LIBERA (total - blocata in ordine) — ca sa nu atinga pozitia botului."""
        bx = self.client._private("BalanceEx").get(asset)
        if not bx:
            return 0.0
        try:
            return float(bx.get("balance", 0)) - float(bx.get("hold_trade", 0))
        except (TypeError, ValueError):
            return 0.0

    # -- trend instant HYPE (din cache_instant_trend.json) — pt filtrele optionale --
    def _trend_value(self, pair: str) -> float:
        """Panta trendului instant (>0 sus, <0 jos, 0 neutru/necunoscut). 0 la orice eroare
        -> filtrele devin no-op (degradare sigura, comportament ca fara filtru)."""
        try:
            import json
            with open(CACHE_TREND) as f:
                snap = json.load(f).get(pair)
            if snap:
                return float(snap.get("gradient_recent", snap.get("slope_small", 0.0)) or 0.0)
        except Exception:
            pass
        return 0.0

    # == contract ADAPTOR pt TrailingCore =====================================
    def assets(self):
        for asset, trail in TRAIL_PCT.items():
            yield (asset, asset, PAIR_FOR.get(asset, asset + "USD"), trail)  # key=asset, pair separat

    def begin_tick(self) -> bool:
        return True   # Kraken citeste balanta per-asset (in free_qty), nu in bloc

    def free_qty(self, asset: str) -> float:
        return self._free(asset)

    def price(self, pair: str):
        return self.client.last_price(pair)

    def trend(self, pair: str) -> float:
        return self._trend_value(pair)

    def execute_sell(self, key, asset, pair, qty, price, peak, trail) -> bool:
        try:
            self.client.add_order(pair, "sell", round(qty, 8),
                                  round(price * 0.995, 4), ordertype="limit")
            self.log(f"  🛑 [TRAIL-K] VANDUT {qty} {asset} @ ~{price:.4f} "
                     f"(varf {peak:.4f}, -{trail}%)")
            notify(title=f"🛑 TRAILING {asset}: vandut {qty:.4f} @ ~{price:.2f}",
                   body=f"Crash >{trail}% de la varf {peak:.2f}. Protectie declansata.",
                   source="kraken-trail", price=price, desktop=False)
            return True
        except KrakenError as e:
            self.log(f"  ! [TRAIL-K] vanzare {asset} esuata: {e}")
            return False

    def execute_rebuy(self, key, asset, pair, qty, price, rb) -> bool:
        try:
            # limit usor PESTE pret -> fill sigur (ca add_order sell e usor sub)
            self.client.add_order(pair, "buy", qty, round(price * 1.005, 4), ordertype="limit")
            self.log(f"  🟢 [TRAIL-K] RE-BUY {qty} {asset} @ ~{price:.4f}  "
                     f"(recul +{REBUY_BOUNCE_PCT}% de la minim {rb['low']:.4f}; vandut la {rb.get('sell_price', 0):.4f})")
            notify(title=f"🟢 RE-BUY {asset}: {qty:.4f} @ ~{price:.2f}",
                   body=f"Recul +{REBUY_BOUNCE_PCT}% de la minimul {rb['low']:.2f} dupa crash sell — reintru.",
                   source="kraken-trail", price=price, desktop=False)
            return True
        except KrakenError as e:
            self.log(f"  ! [TRAIL-K] re-buy {asset} esuat: {e}")
            return False                                      # pastreaza rebuy -> reincearca data viitoare

    def log_dry_sell(self, key, asset, pair, qty, price, peak, trail) -> None:
        self.log(f"  🟡 [TRAIL-K][DRY] AR VINDE {qty} {asset} @ ~{price:.4f} "
                 f"(varf {peak:.4f}, -{trail}%)  [KRAKEN_TRAILING_ENABLED=true ca sa execute]")

    def log_dry_rebuy(self, key, asset, pair, qty, price, rb) -> None:
        self.log(f"  🟡 [TRAIL-K][DRY] AR RE-CUMPARA {qty} {asset} @ ~{price:.4f}  "
                 f"(recul de la minim {rb['low']:.4f})  [KRAKEN_TRAILING_ENABLED=true ca sa execute]")

    def log_hold(self, key, asset, pair, price, peak, stop_at, trail, free) -> None:
        self.log(f"  [TRAIL-K] {asset}: {price:.4f}  varf {peak:.4f}  "
                 f"vinde sub {stop_at:.4f} (-{trail}%)  (liber {free:.4f})")

    def log_skip_rebuy_trend(self, asset) -> None:
        self.log(f"  [TRAIL-K] re-buy {asset} amanat — trend instant CLAR jos (nu prind cutitul)")

    def log_skip_sell_trend(self, key, asset, pair, trail) -> None:
        self.log(f"  [TRAIL-K] {asset}: -{trail}% atins dar trend instant SUS — NU vand (anti-wick)")

    def log_tick_error(self, e) -> None:
        self.log(f"  ! [TRAIL-K] ciclu esuat ({e.__class__.__name__}: {e}) — reincerc")

    # -- un pas / bucla --------------------------------------------------------
    def check_once(self) -> None:
        self.core.check_once()

    def run(self):
        mode = "⚠ ACTIV (vinde real)" if self.enabled else "DRY-RUN (doar logheaza)"
        self.log(f"=== TRAILING STOP KRAKEN pornit — {mode} ===")
        self.log(f"    protejez: " + ", ".join(f"{a}={t}%" for a, t in TRAIL_PCT.items()) +
                 "  (doar balanta LIBERA, nu pozitia botului)")
        self.log(f"    re-buy: {'ON' if REBUY_ENABLED else 'off'} (recul +{REBUY_BOUNCE_PCT}% de la minim)")
        while True:
            self.check_once()
            time.sleep(CHECK_SECONDS)


def main() -> int:
    load_dotenv(os.path.join(_HERE, ".env"))
    load_dotenv(os.path.join(_HERE, "config.env"))
    ap = argparse.ArgumentParser(description="Trailing stop disjunctor pe Kraken (cu re-buy).")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    # Cheie dedicata trailing (KRAKEN_API_KEY_TRAIL) -> nonce separat de _BOT (kraken_bot + xstock_watch).
    # Fallback pe _BOT daca _TRAIL lipseste din .env.
    key    = os.environ.get("KRAKEN_API_KEY_TRAIL")    or os.environ.get("KRAKEN_API_KEY_BOT")
    secret = os.environ.get("KRAKEN_API_SECRET_TRAIL") or os.environ.get("KRAKEN_API_SECRET_BOT")
    client = KrakenClient(key, secret)
    ts = KrakenTrailing(client)
    if args.status:
        st = ts._load()
        for a, t in TRAIL_PCT.items():
            e = st.get(a, {})
            peak = e.get("peak")
            rb = e.get("rebuy")
            print(f"{a}: varf={peak} trailing={t}% " +
                  (f"vinde sub {peak*(1-t/100):.4f}" if peak else "(fara varf inca)") +
                  (f"  | re-buy ARMAT (qty {rb['qty']}, min {rb.get('low')})" if rb else ""))
        print(f"ENABLED={ts.enabled}  REBUY={REBUY_ENABLED} (bounce {REBUY_BOUNCE_PCT}%)")
        return 0
    if args.once:
        ts.check_once()
        return 0
    ts.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

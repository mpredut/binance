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

  python3 trailing_stop.py        # bucla (enabled din trailing.conf)
  python3 trailing_stop.py --once                              # o verificare
  python3 trailing_stop.py --status                            # varfuri + praguri
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from common import load_dotenv, log, float_env
from kraken_client import KrakenClient, KrakenError
from notify import notify

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
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


def should_sell(current: float, peak: float, trail_pct: float) -> bool:
    return peak > 0 and trail_pct > 0 and current <= peak * (1 - trail_pct / 100.0)


class KrakenTrailing:
    def __init__(self, client: KrakenClient, log=log, enabled=None, state_file=STATE_FILE):
        self.client = client
        self.log = log
        self.enabled = (os.environ.get("KRAKEN_TRAILING_ENABLED", "false").lower() == "true"
                        if enabled is None else enabled)
        self.state_file = state_file

    def _load(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(st, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            self.log(f"  ! [TRAIL-K] nu pot salva: {e}")

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
            with open(CACHE_TREND) as f:
                snap = json.load(f).get(pair)
            if snap:
                return float(snap.get("gradient_recent", snap.get("slope_small", 0.0)) or 0.0)
        except Exception:
            pass
        return 0.0

    # -- re-buy dupa crash sell -----------------------------------------------
    def _handle_rebuy(self, asset: str, pair: str, st: dict, price: float) -> None:
        """Recumparare dupa stop-loss de crash: cand pretul revine REBUY_BOUNCE_PCT% de la minimul
        de dupa vanzare (confirma ca s-a oprit caderea), recumpara qty vanduta."""
        rb = st.get("rebuy")
        if not rb:
            return
        rb["low"] = min(rb.get("low", price), price)          # urmareste fundul de dupa vanzare
        if price < rb["low"] * (1 + REBUY_BOUNCE_PCT / 100.0):
            return                                            # reculul inca neconfirmat
        if REBUY_SKIP_IF_TREND_DOWN and self._trend_value(pair) < 0:
            self.log(f"  [TRAIL-K] re-buy {asset} amanat — trend instant CLAR jos (nu prind cutitul)")
            return
        qty = round(float(rb.get("qty", 0)), 8)
        if qty <= 0:
            st.pop("rebuy", None)
            return
        if self.enabled and qty * price >= MIN_NOTIONAL_USD:
            try:
                # limit usor PESTE pret -> fill sigur (ca add_order sell e usor sub)
                self.client.add_order(pair, "buy", qty, round(price * 1.005, 4), ordertype="limit")
                self.log(f"  🟢 [TRAIL-K] RE-BUY {qty} {asset} @ ~{price:.4f}  "
                         f"(recul +{REBUY_BOUNCE_PCT}% de la minim {rb['low']:.4f}; vandut la {rb.get('sell_price', 0):.4f})")
                notify(title=f"🟢 RE-BUY {asset}: {qty:.4f} @ ~{price:.2f}",
                       body=f"Recul +{REBUY_BOUNCE_PCT}% de la minimul {rb['low']:.2f} dupa crash sell — reintru.",
                       source="kraken-trail", price=price, desktop=False)
            except KrakenError as e:
                self.log(f"  ! [TRAIL-K] re-buy {asset} esuat: {e}")
                return                                        # pastreaza rebuy -> reincearca data viitoare
        else:
            self.log(f"  🟡 [TRAIL-K][DRY] AR RE-CUMPARA {qty} {asset} @ ~{price:.4f}  "
                     f"(recul de la minim {rb['low']:.4f})  [KRAKEN_TRAILING_ENABLED=true ca sa execute]")
        st.pop("rebuy", None)                                 # 1 transa -> gata

    def check_once(self) -> None:
        try:
            state = self._load()
            for asset, trail in TRAIL_PCT.items():
                pair = PAIR_FOR.get(asset, asset + "USD")
                free = self._free(asset)
                price = self.client.last_price(pair)
                if not price or price <= 0:
                    continue
                st = state.setdefault(asset, {"peak": price})
                if REBUY_ENABLED and st.get("rebuy"):     # re-buy pending — INAINTE de check-ul de notional (free~0 dupa vanzare)
                    self._handle_rebuy(asset, pair, st, price)
                if free * price < MIN_NOTIONAL_USD:
                    continue                              # nimic de protejat
                if price > st["peak"]:
                    st["peak"] = price
                stop_at = st["peak"] * (1 - trail / 100.0)
                if should_sell(price, st["peak"], trail):
                    if SELL_SKIP_IF_TREND_UP and self._trend_value(pair) > 0:
                        self.log(f"  [TRAIL-K] {asset}: -{trail}% atins dar trend instant SUS — NU vand (anti-wick)")
                        continue
                    if self.enabled:
                        try:
                            self.client.add_order(pair, "sell", round(free, 8),
                                                  round(price * 0.995, 4), ordertype="limit")
                            self.log(f"  🛑 [TRAIL-K] VANDUT {free} {asset} @ ~{price:.4f} "
                                     f"(varf {st['peak']:.4f}, -{trail}%)")
                            notify(title=f"🛑 TRAILING {asset}: vandut {free:.4f} @ ~{price:.2f}",
                                   body=f"Crash >{trail}% de la varf {st['peak']:.2f}. Protectie declansata.",
                                   source="kraken-trail", price=price, desktop=False)
                            st["peak"] = price                # re-armeaza de la pretul curent
                            if REBUY_ENABLED:                 # armeaza re-buy: recumpara cand pretul revine de la minim
                                st["rebuy"] = {"qty": round(free, 8), "sell_price": price, "low": price}
                        except KrakenError as e:
                            self.log(f"  ! [TRAIL-K] vanzare {asset} esuata: {e}")
                    else:
                        self.log(f"  🟡 [TRAIL-K][DRY] AR VINDE {free} {asset} @ ~{price:.4f} "
                                 f"(varf {st['peak']:.4f}, -{trail}%)  [KRAKEN_TRAILING_ENABLED=true ca sa execute]")
                else:
                    self.log(f"  [TRAIL-K] {asset}: {price:.4f}  varf {st['peak']:.4f}  "
                             f"vinde sub {stop_at:.4f} (-{trail}%)  (liber {free:.4f})")
            self._save(state)
        except Exception as e:  # noqa: BLE001 — rezilienta: net picat -> reincearca
            self.log(f"  ! [TRAIL-K] ciclu esuat ({e.__class__.__name__}: {e}) — reincerc")

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
    client = KrakenClient(os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET"))
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

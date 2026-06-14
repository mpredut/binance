#!/usr/bin/env python3
"""
trailing_stop.py (Kraken) — DISJUNCTOR DE CRASH pe holdingurile manuale de pe Kraken.

Protejeaza HYPE-ul cumparat manual (~$1.7k) impotriva unui colaps sustinut, FARA
sa-ti capeze upside-ul (prag larg) si fara whipsaw (15%, nu 7%). Vinde DOAR
balanta LIBERA (free = total - hold_trade), deci NU atinge cei 3.38 HYPE blocati
in ordinul de TP al botului — separare curata.

ONEST (vezi walk-forward in conversatie): trailing-ul nu produce alfa; rolul lui
e protectia de crash. Prag larg ~15% = se declanseaza doar la o cadere reala.

  KRAKEN_TRAILING_ENABLED=true python3 trailing_stop.py        # bucla (vinde real)
  python3 trailing_stop.py --once                              # o verificare (dry-run)
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
STATE_FILE = os.path.join(_HERE, "trailing_state.json")

# prag larg per ASSET (disjunctor de crash, nu unealta de profit) + perechea de vanzare
TRAIL_PCT = {"HYPE": 15.0}
PAIR_FOR = {"HYPE": "HYPEUSD"}
DEFAULT_TRAIL_PCT = 15.0
MIN_NOTIONAL_USD = 10.0
CHECK_SECONDS = float(os.environ.get("KRAKEN_TRAILING_CHECK_SECONDS", "120"))


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

    def check_once(self) -> None:
        try:
            state = self._load()
            for asset, trail in TRAIL_PCT.items():
                pair = PAIR_FOR.get(asset, asset + "USD")
                free = self._free(asset)
                price = self.client.last_price(pair)
                if not price or price <= 0 or free * price < MIN_NOTIONAL_USD:
                    continue
                st = state.setdefault(asset, {"peak": price})
                if price > st["peak"]:
                    st["peak"] = price
                stop_at = st["peak"] * (1 - trail / 100.0)
                if should_sell(price, st["peak"], trail):
                    if self.enabled:
                        try:
                            self.client.add_order(pair, "sell", round(free, 8),
                                                  round(price * 0.995, 4), ordertype="limit")
                            self.log(f"  🛑 [TRAIL-K] VANDUT {free} {asset} @ ~{price:.4f} "
                                     f"(varf {st['peak']:.4f}, -{trail}%)")
                            notify(title=f"🛑 TRAILING {asset}: vandut {free:.4f} @ ~{price:.2f}",
                                   body=f"Crash >{trail}% de la varf {st['peak']:.2f}. Protectie declansata.",
                                   source="kraken-trail", price=price, desktop=False)
                            st["peak"] = price
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
        while True:
            self.check_once()
            time.sleep(CHECK_SECONDS)


def main() -> int:
    load_dotenv(os.path.join(_HERE, ".env"))
    load_dotenv(os.path.join(_HERE, "config.env"))
    ap = argparse.ArgumentParser(description="Trailing stop disjunctor pe Kraken.")
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
            print(f"{a}: varf={peak} trailing={t}% " +
                  (f"vinde sub {peak*(1-t/100):.4f}" if peak else "(fara varf inca)"))
        print(f"ENABLED={ts.enabled}")
        return 0
    if args.once:
        ts.check_once()
        return 0
    ts.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

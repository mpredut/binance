#!/usr/bin/env python3
"""
trailing_stop.py (Kraken) — crash circuit breaker for manual Kraken holdings.

Protects manually purchased HYPE (~$1.7k) against a sustained collapse without
capping upside (wide threshold) or causing tight-stop whipsaw (15%, not 7%). It
sells only the FREE balance (free = total - hold_trade), so it does not touch the
3.38 HYPE locked in the bot's take-profit order, maintaining clean separation.

Walk-forward analysis shows trailing does not generate alpha; its role is crash
protection. A wide ~15% threshold triggers only on a material decline.

Re-buy after a crash sale mirrors binance_api/trailing_stop.py: after a forced
crash sale, it buys back when price rebounds REBUY_BOUNCE_PCT% from the post-sale
low, confirming the fall has stopped before entry. Optional trend filters use the
HYPE cache_instant_trend data. Configuration is in kraken/trailing.conf.

The trailing and re-buy state machine is in trailing_core.TrailingCore and shared
with Binance. This module is only the Kraken ADAPTER for provider-specific API,
logging, and notifications. See kraken/test_trailing_kraken.py.

  python3 trailing_stop.py        # loop (enabled in trailing.conf)
  python3 trailing_stop.py --once                              # one check
  python3 trailing_stop.py --status                            # peaks and thresholds
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from kraken_common import load_dotenv, log, float_env, single_instance
from kraken_client import KrakenClient, KrakenError
from notify import notify

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)   # import the shared core from the repository root

from trailing_core import TrailingCore, should_sell  # noqa: E402  (re-export should_sell for tests/compatibility)

STATE_FILE = os.path.join(_HERE, "trailing_state.json")
CACHE_TREND = os.path.join(_ROOT, "cachedb", "cache_instant_trend.json")


# Load KEY=VALUE configuration from kraken/trailing.conf; external environment values
# override it for ad-hoc testing. Use the shared load_dotenv implementation.
load_dotenv(os.path.join(_HERE, "trailing.conf"))

# Wide threshold per asset (crash circuit breaker, not a profit tool) and sale pair.
TRAIL_PCT = {"HYPE": 18.0}   # August 4: 15->18 by user decision; wider means less whipsaw
PAIR_FOR = {"HYPE": "HYPEUSD"}
DEFAULT_TRAIL_PCT = 15.0
MIN_NOTIONAL_USD = 10.0
CHECK_SECONDS = float(os.environ.get("KRAKEN_TRAILING_CHECK_SECONDS", "120"))

# After a crash sale, re-buy when price rebounds BOUNCE% from the post-sale low.
REBUY_ENABLED = os.environ.get("KRAKEN_TRAILING_REBUY_ENABLED", "true").lower() == "true"
REBUY_BOUNCE_PCT = float(os.environ.get("KRAKEN_TRAILING_REBUY_BOUNCE_PCT", "1.2"))
# Trend filters use HYPE cache_instant_trend and act only on a CLEAR opposing signal.
# Neutral/unknown does not block, providing safe degradation. Skip re-buy on a clear
# downtrend. Crash sells are unfiltered by default for reliability; true enables anti-wick.
REBUY_SKIP_IF_TREND_DOWN = os.environ.get("KRAKEN_TRAILING_REBUY_SKIP_IF_TREND_DOWN", "true").lower() == "true"
SELL_SKIP_IF_TREND_UP = os.environ.get("KRAKEN_TRAILING_SELL_SKIP_IF_TREND_UP", "false").lower() == "true"
# Minimum profit before trailing activates (0 means immediate activation as before).
# This prevents selling at a loss after a normal dip immediately following a purchase.
MIN_PROFIT_PCT = float(os.environ.get("KRAKEN_TRAILING_MIN_PROFIT_PCT", "0.0"))


class KrakenTrailing:
    """Kraken adapter for TrailingCore, providing free-balance, price,
    limit sell/buy, and trend APIs plus provider-specific logging and notifications."""

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

    # -- state delegated to the core; retained for --status and tests ----------
    def _load(self) -> dict:
        return self.core.load()

    def _save(self, st: dict):
        self.core.save(st)

    def trail_pct_for(self, asset: str) -> float:
        return TRAIL_PCT.get(asset, DEFAULT_TRAIL_PCT)

    def _free(self, asset: str) -> float:
        """Return FREE balance (total minus order holds), excluding the bot position."""
        bx = self.client._private("BalanceEx").get(asset)
        if not bx:
            return 0.0
        try:
            return float(bx.get("balance", 0)) - float(bx.get("hold_trade", 0))
        except (TypeError, ValueError):
            return 0.0

    # -- HYPE instant trend from cache_instant_trend.json for optional filters --
    def _trend_value(self, pair: str) -> float:
        """Return instant-trend slope (>0 up, <0 down, 0 neutral/unknown).
        Errors return 0, making trend filters a safe no-op."""
        try:
            import json
            with open(CACHE_TREND) as f:
                snap = json.load(f).get(pair)
            if snap:
                return float(snap.get("gradient_recent", snap.get("slope_small", 0.0)) or 0.0)
        except Exception:
            pass
        return 0.0

    # == TrailingCore ADAPTER contract ========================================
    def assets(self):
        for asset, trail in TRAIL_PCT.items():
            yield (asset, asset, PAIR_FOR.get(asset, asset + "USD"), trail)  # key=asset, separate pair

    def begin_tick(self) -> bool:
        return True   # Kraken reads balances per asset in free_qty, not in bulk

    def free_qty(self, asset: str) -> float:
        return self._free(asset)

    def price(self, pair: str):
        return self.client.last_price(pair)

    def trend(self, pair: str) -> float:
        return self._trend_value(pair)

    def execute_sell(self, key, asset, pair, qty, price, peak, trail) -> bool:
        try:
            # Price slightly BELOW market for reliable fill. Central add_order applies
            # pair_decimals precision instead of hard-coding it here, avoiding rejection.
            self.client.add_order(pair, "sell", round(qty, 8),
                                  price * 0.995, ordertype="limit")
            self.log(f"  🛑 [TRAIL-K] VANDUT {qty} {asset} @ ~{price:.4f} "
                     f"(varf {peak:.4f}, -{trail}%)")
            notify(title=f"🛑 TRAILING {asset} vandut {qty:.2f}@~{price:.2f}",
                   body=f"crash >{trail}% de la varf {peak:.2f}",
                   source="kraken-trail", price=price, desktop=False)
            return True
        except KrakenError as e:
            self.log(f"  ! [TRAIL-K] vanzare {asset} esuata: {e}")
            return False

    def execute_rebuy(self, key, asset, pair, qty, price, rb) -> bool:
        try:
            # Limit slightly ABOVE market for reliable fill, symmetric with the sell order.
            # Central add_order applies pair_decimals rather than hard-coded precision.
            self.client.add_order(pair, "buy", qty, price * 1.005, ordertype="limit")
            self.log(f"  🟢 [TRAIL-K] RE-BUY {qty} {asset} @ ~{price:.4f}  "
                     f"(recul +{REBUY_BOUNCE_PCT}% de la minim {rb['low']:.4f}; vandut la {rb.get('sell_price', 0):.4f})")
            notify(title=f"🟢 RE-BUY {asset} {qty:.2f}@~{price:.2f}",
                   body=f"recul +{REBUY_BOUNCE_PCT}% de la min {rb['low']:.2f} dupa crash",
                   source="kraken-trail", price=price, desktop=False)
            return True
        except KrakenError as e:
            self.log(f"  ! [TRAIL-K] re-buy {asset} esuat: {e}")
            return False                                      # preserve re-buy state and retry next time

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

    # -- one step / loop -------------------------------------------------------
    def check_once(self) -> None:
        self.core.check_once()
        # healthcheck.sh uses the log mtime as its heartbeat. When there is no free
        # balance to protect, the core intentionally exits without another message;
        # explicitly confirm that the process still completed the cycle successfully.
        self.log("  [TRAIL-K] heartbeat")

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
    if not (args.once or args.status):
        single_instance("kraken_trailing")
    # A dedicated trailing key (KRAKEN_API_KEY_TRAIL) keeps its nonce separate from
    # _BOT (kraken_bot + xstock_watch). Fall back to _BOT when _TRAIL is absent.
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

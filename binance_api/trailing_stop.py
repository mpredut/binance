#!/usr/bin/env python3
"""
trailing_stop.py — per-coin trailing stop for Binance holdings.

Why it exists: assetguardian.sell_all_assets() triggers correctly but calls
place_safe_order(force=False), which passes through apply_weight_limit. During an
uptrend the counter-trend weight is 0.02, reducing the order to zero and producing
"Orders sent: 0". The protection mechanism therefore sells nothing. The trailing stop:
  * holds the position while price rises and tracks the peak;
  * sells only when price falls trail% from the peak, protecting realized gains;
  * uses force=True to bypass weighting, which would otherwise zero the order.

Walk-forward out-of-sample results on a 291-day real feed show that a TIGHT
trailing stop does not beat holding: rebounds during declines create whipsaw and
fees. It is not a profit source. Its intended role is a CRASH CIRCUIT BREAKER with
a WIDE threshold (~22%), triggered only by a sustained collapse as protection
against the scenario that destroys the holding. Run it in dry-run first; the rest
of the strategy (hold+DCA+weighting) remains unchanged.

The trailing and re-buy state machine is in trailing_core.TrailingCore and shared
with Kraken. This module is only the Binance ADAPTER (API and provider-specific
logging). See tests/test_trailing_stop.py for behavior coverage.

SAFETY:
  * TRAILING_ENABLED=false (default) enables DRY-RUN and only logs proposed sales.
  * It operates only on coins listed in symbols.py.
  * The peak is persisted across restarts and is not reset.
  * Orders below the minimum notional are skipped.

  TRAILING_ENABLED=true python trailing_stop.py            # loop
  python trailing_stop.py --once                            # one check (dry-run)
  python trailing_stop.py --status                          # current peaks and thresholds
"""

from __future__ import annotations

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # binance_api/ -> repository root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)   # also support direct execution (python binance_api/trailing_stop.py)

from providers.quantity import resolve_assets

from trailing_core import TrailingCore, should_sell  # noqa: E402  (re-export should_sell for tests/compatibility)
from botcore import load_dotenv, single_instance  # noqa: E402  (shared KEY=VALUE parser and single-instance guard)

DEFAULT_STATE = os.path.join(_ROOT, "cachedb", "trailing_state.json")


# Load KEY=VALUE configuration from trailing.conf into the environment; external values override it.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trailing.conf"))

# A WIDE THRESHOLD is a CRASH CIRCUIT BREAKER, not a profit tool.
# Walk-forward out-of-sample analysis on a real 291-day feed showed that tight
# trailing stops (8-12%) do not beat holding because violent rebounds cause whipsaw
# and fees. The useful role is protection against a sustained collapse: a wide
# threshold (~22%) triggers only on a catastrophic fall, not market noise.
TRAIL_PCT = {
    "BTCUSDC": 20.0,
    "TAOUSDC": 22.0,
}
DEFAULT_TRAIL_PCT = 22.0
SELL_FRACTION = float(os.environ.get("TRAILING_SELL_FRACTION", "1.0"))  # 1.0=all, 0.5=half
MIN_NOTIONAL_USD = 11.0
CHECK_SECONDS = float(os.environ.get("TRAILING_CHECK_SECONDS", "60"))

# Re-buy after a crash stop-loss: the trailing component that sold with a bypass also
# buys back with a bypass, avoiding the profit guard whose long window would block
# re-entry. Trigger when price rebounds REBUY_BOUNCE_PCT% from the post-sale low,
# confirming the fall has stopped before entry. One tranche is supported currently;
# REBUY_TRANCHES is reserved for a future dip-DCA extension.
REBUY_ENABLED = os.environ.get("TRAILING_REBUY_ENABLED", "true").lower() == "true"
REBUY_BOUNCE_PCT = float(os.environ.get("TRAILING_REBUY_BOUNCE_PCT", "1.2"))
REBUY_TRANCHES = int(os.environ.get("TRAILING_REBUY_TRANCHES", "1"))
# Trend filters read cache_instant_trend through cacheManager. They act only on a
# CLEAR opposing signal; neutral/unknown does not block, safely degrading to behavior
# without a filter. Skip re-buy on a clear downtrend. Crash sells are unfiltered by
# default so the circuit breaker remains reliable; enable the sell filter to avoid
# selling while the instant trend is clearly up (anti-wick behavior).
REBUY_SKIP_IF_TREND_DOWN = os.environ.get("TRAILING_REBUY_SKIP_IF_TREND_DOWN", "true").lower() == "true"
SELL_SKIP_IF_TREND_UP = os.environ.get("TRAILING_SELL_SKIP_IF_TREND_UP", "false").lower() == "true"
# Minimum profit before trailing activates (0 means immediate activation as before).
# This prevents selling at a loss after a normal dip immediately following a purchase.
MIN_PROFIT_PCT = float(os.environ.get("TRAILING_MIN_PROFIT_PCT", "0.0"))


class TrailingStop:
    """Binance adapter for TrailingCore, providing balance, price, sell/buy,
    trend APIs, and provider-specific logging. TrailingCore owns the state machine."""

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

    # -- state delegated to the core; retained for --status and tests ----------
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

    # -- instant trend from cacheManager for optional filters ------------------
    def _trend_value(self, symbol: str) -> float:
        """Return instant-trend slope (>0 up, <0 down, 0 neutral/unknown).
        Errors return 0, making trend filters a safe no-op."""
        try:
            import cacheManager as cm
            snap = cm.get_short_trend_manager().get_snapshot(symbol)
            if snap:
                return float(snap.get('gradient_recent', snap.get('slope_small', 0.0)) or 0.0)
        except Exception:
            pass
        return 0.0

    # == TrailingCore ADAPTER contract ========================================
    def assets(self):
        for symbol in self.sym.symbols:
            asset = resolve_assets(symbol)[0]
            yield (symbol, asset, symbol, self.trail_pct_for(symbol))  # key=pair=symbol on Binance

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
        # July 30: use the single guarded proxy (self.po = market_api.api, .place()).
        # force=True sells at MARKET for reliable crash execution.
        # bypass_profit_guard=True bypasses profit/history protection because this is a
        # STOP-LOSS below the last buy; otherwise the guard would block it. Daily limits
        # and cooldown remain active as before; the bypass skips only profit and weighting.
        self.po.place(pair, "SELL", price, qty, force=True, bypass_profit_guard=True, smart=False)
        self.log(f"  🛑 [TRAIL] VANDUT {pair} {qty} @ ~{price:.4f} "
                 f"(varf {peak:.4f}, -{trail}%)")
        return True

    def execute_rebuy(self, key, asset, pair, qty, price, rb) -> bool:
        self.po.place(pair, "BUY", price, qty, force=True, bypass_profit_guard=True, smart=False)
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

    # -- one step / loop -------------------------------------------------------
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
    if not (args.once or args.status):
        single_instance("binance_trailing")

    from binance_api import bapi as api
    from providers.market_api import api as po   # single guarded .place() proxy; formerly bapi_placeorder
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

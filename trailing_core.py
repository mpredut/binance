#!/usr/bin/env python3
"""
trailing_core.py — provider-agnostic trailing-stop core with re-buy support.

Why it exists: binance_api/trailing_stop.py and kraken/trailing_stop.py used the
SAME state machine (track the peak -> sell at trail% below the peak -> re-buy on
a bounce from the post-sale low), copied almost line for line. The shared CONTROL
logic lives here; each provider remains a thin ADAPTER that performs only its own
API calls (price, balance, sell, buy, trend, notify) and provider-specific logging.

Behavior is IDENTICAL to the pre-extraction implementation, as covered by
tests/test_trailing_stop.py and kraken/test_trailing_kraken.py. Those unchanged
tests exercise the TrailingStop and KrakenTrailing adapters through the same
state machine, which is now centralized here.

State schema (per key, persisted by the core and UNCHANGED from the old version,
so existing daemon state files continue to load):
  { "<key>": { "peak": float, "rebuy": {"qty","sell_price","low"}  (optional) } }

ADAPTER contract (duck typing; see the two adapter classes):
  assets()        -> iterable of (key, asset, pair, trail_pct)
  begin_tick()    -> bool          # False skips the tick (e.g. unavailable balances)
  free_qty(asset) -> float         # FREE quantity to protect
  price(pair)     -> float | None
  trend(pair)     -> float         # >0 up, <0 down, 0 neutral/unknown (filters are no-op at 0)
  execute_sell(key, asset, pair, qty, price, peak, trail) -> bool  # place+log+notify; True=success
  execute_rebuy(key, asset, pair, qty, price, rb)         -> bool  # same; False preserves re-buy for retry
  log_dry_sell / log_dry_rebuy / log_hold / log_skip_rebuy_trend /
  log_skip_sell_trend / log_item_error / log_tick_error            # logging only (provider-specific wording)
"""

from __future__ import annotations

import json
import os


def should_sell(current: float, peak: float, trail_pct: float) -> bool:
    """Return True when the price has fallen at least trail% from the peak."""
    return peak > 0 and trail_pct > 0 and current <= peak * (1 - trail_pct / 100.0)


class TrailingCore:
    def __init__(self, adapter, *, log, enabled, state_file, min_notional,
                 rebuy_enabled, rebuy_bounce_pct, rebuy_skip_if_trend_down,
                 sell_skip_if_trend_up, sell_fraction=1.0, item_isolation=True,
                 min_profit_pct=0.0):
        self.a = adapter
        self.log = log
        self.enabled = enabled
        self.state_file = state_file
        self.min_notional = min_notional
        self.rebuy_enabled = rebuy_enabled
        self.rebuy_bounce_pct = rebuy_bounce_pct
        self.rebuy_skip_if_trend_down = rebuy_skip_if_trend_down
        self.sell_skip_if_trend_up = sell_skip_if_trend_up
        self.sell_fraction = sell_fraction
        self.min_profit_pct = min_profit_pct
        # item_isolation=True (Binance): catch errors per coin and always save after the loop
        # (one failed coin does not stop the rest). False (Kraken): wrap the entire tick;
        # on error, log without saving and retry next time. This preserves each provider's
        # original error-handling structure.
        self.item_isolation = item_isolation

    # -- state (peak per key) -------------------------------------------------
    def load(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, state: dict) -> None:
        try:
            d = os.path.dirname(self.state_file)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            self.log(f"  ! [TRAIL] nu pot salva starea: {e}")

    # -- re-buy after a crash sale --------------------------------------------
    def _handle_rebuy(self, key, asset, pair, st: dict, price: float) -> None:
        """Re-buy after a crash stop-loss once price bounces rebuy_bounce_pct%
        from the post-sale low, confirming the fall has stopped before entry."""
        rb = st.get("rebuy")
        if not rb:
            return
        rb["low"] = min(rb.get("low", price), price)          # track the post-sale low
        if price < rb["low"] * (1 + self.rebuy_bounce_pct / 100.0):
            return                                            # bounce is not confirmed yet; wait
        if self.rebuy_skip_if_trend_down and self.a.trend(pair) < 0:
            self.a.log_skip_rebuy_trend(asset)
            return
        qty = round(float(rb.get("qty", 0)), 8)               # one tranche equals the full quantity sold
        if qty <= 0:
            st.pop("rebuy", None)
            return
        if self.enabled and qty * price >= self.min_notional:
            if not self.a.execute_rebuy(key, asset, pair, qty, price, rb):
                return                                        # failed: retain re-buy state and retry next time
        else:
            self.a.log_dry_rebuy(key, asset, pair, qty, price, rb)
        st.pop("rebuy", None)                                 # one tranche; complete
        if self.min_profit_pct > 0:
            st["warmup_at"] = price * (1 + self.min_profit_pct / 100.0)  # restart warm-up from the re-buy price

    # -- one asset ------------------------------------------------------------
    def _process(self, key, asset, pair, trail, state) -> None:
        free = self.a.free_qty(asset)
        price = self.a.price(pair)
        if not price or price <= 0:
            return
        is_new = key not in state
        st = state.setdefault(key, {"peak": price})
        if is_new and self.min_profit_pct > 0:
            st["warmup_at"] = price * (1 + self.min_profit_pct / 100.0)  # first tick: set activation threshold
        if self.rebuy_enabled and st.get("rebuy"):            # handle pending re-buy BEFORE notional check (free~0 after sale)
            self._handle_rebuy(key, asset, pair, st, price)
        if free * price < self.min_notional:
            return                                            # nothing to protect
        if "warmup_at" in st:
            # Warm up until price exceeds the threshold set on the first tick or after re-buy.
            if price < st["warmup_at"]:
                self.log(f"  [TRAIL] {asset}: {price:.4f}  warming up — activ la {st['warmup_at']:.4f}"
                         f" (+{self.min_profit_pct}%)")
                return
            st.pop("warmup_at")                              # threshold reached; trailing is now active
        if price > st["peak"]:
            st["peak"] = price                                # new peak moves the trailing level up
        stop_at = st["peak"] * (1 - trail / 100.0)
        if should_sell(price, st["peak"], trail):
            if self.sell_skip_if_trend_up and self.a.trend(pair) > 0:
                self.a.log_skip_sell_trend(key, asset, pair, trail)
                return
            sell_qty = round(free * self.sell_fraction, 8)
            if self.enabled and sell_qty * price >= self.min_notional:
                if self.a.execute_sell(key, asset, pair, sell_qty, price, st["peak"], trail):
                    st["peak"] = price                        # re-arm from the current price
                    if self.rebuy_enabled:                    # arm re-buy for a bounce from the low
                        st["rebuy"] = {"qty": sell_qty, "sell_price": price, "low": price}
            else:
                self.a.log_dry_sell(key, asset, pair, sell_qty, price, st["peak"], trail)
        else:
            self.a.log_hold(key, asset, pair, price, st["peak"], stop_at, trail, free)

    # -- one step -------------------------------------------------------------
    def check_once(self) -> None:
        if not self.a.begin_tick():
            return
        if self.item_isolation:                               # Binance: isolate each coin and always save
            state = self.load()
            for key, asset, pair, trail in self.a.assets():
                try:
                    self._process(key, asset, pair, trail, state)
                except Exception as e:  # noqa: BLE001 — one coin must not stop the rest
                    self.a.log_item_error(key, e)
            self.save(state)
        else:                                                 # Kraken: one try per tick; retry errors without saving
            try:
                state = self.load()
                for key, asset, pair, trail in self.a.assets():
                    self._process(key, asset, pair, trail, state)
                self.save(state)
            except Exception as e:  # noqa: BLE001 — resilience: retry after a network failure
                self.a.log_tick_error(e)

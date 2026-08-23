# Tradeall policy and operational boundaries

`tradeall.py` consumes timestamped price snapshots and two trend windows. The
`TrendCoordinator` evaluates a symbol only when it is dirty and the minimum interval has
passed, or on the heartbeat ceiling. Duplicate configured symbols are collapsed so one
tick cannot subscribe or evaluate the same market twice.

## Decision pipeline

1. `CacheCurrentPriceManager` supplies a timestamped price. A stale, future, missing,
   non-finite or non-positive price cannot reach signal evaluation or execution.
2. `handle_symbol` calculates short/big-window slope, gradient and position fields.
3. `logic` maintains an UP/DOWN `TrendState`, confirmation count, freshness and uniformity.
4. The Kalman signal gates normal orders and may initiate configured primary transitions.
   Missing/stale Kalman data preserves the existing fail-open business policy.
5. `_fire_order` validates side and price, then submits only through `MarketApi.place` and
   the common `Instrument.place` guard pipeline.

## Retry and submission semantics

Tradeall preserves the historical common persistent retry policy. A refused placement may
enter the generic outbox, while the in-process trend logic also observes its per-trend retry
interval. The retry worker refreshes price and traverses the common guard pipeline before a
new submission. Changing this ownership to strategy-only retry would change financial
behavior and therefore requires a separate explicit decision.

The per-trend maximum counts accepted submissions for anti-spam purposes. Acceptance is
not called a fill and does not prove executed quantity or profit. Daily cap, profit guard,
quantity reconciliation, exchange filters and the shared rapid-fire cooldown still apply.

## Bounds and lifecycle

- coordinator dictionaries are bounded by the configured symbol set;
- trend state contains fixed-size counters/timestamps;
- Kalman fed-price history is capped in `shadow_signals`;
- decision and shadow logs rotate daily and are covered by logger compression/deletion;
- `stop()` wakes the event loop for deterministic test/process shutdown.

## Remaining non-trivial risk

Trend confirmation and accepted-submission counters are in memory. A process restart can
forget the current trend's decision identity and anti-spam budget. The common exchange
cooldown still reduces immediate duplicates, but complete restart safety requires a
durable decision/intent record plus reconciliation of open, partial and terminal venue
orders. That change belongs with the fleet-wide typed lifecycle work and must not be
implemented as a quick local retry patch.

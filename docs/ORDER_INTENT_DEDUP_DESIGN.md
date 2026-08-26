# Order intent deduplication design

## Current operational decision

`RETRY_DEDUP=false` is intentional. The retry outbox must retain every persisted
intent until producers supply a stable semantic identity. The former deduplication
key, `(symbol, side)`, was not idempotency: a newer order from another strategy could
replace the older record's quantity, price, options, and client ID revision while the
older age and attempt counters survived. That could silently lose one financial
intent and leave a hybrid record representing neither producer correctly.

With deduplication disabled, each enqueue receives its own record ID and deterministic
client order ID. Leasing, retry, and resolution operate on that exact record/revision.
The trade-off is that a producer which emits the same logical signal repeatedly can
create multiple records. Queue bounds, guards, cooldowns, price validation, and
venue client IDs reduce the operational risk, but they do not prove semantic equality.

## Target key

Future deduplication must use an explicit `intent_id`, never `symbol + side`.
The canonical identity should be derived or persisted from:

```text
origin + venue/account + symbol + strategy_cycle/campaign + kind/tier + side
```

Examples:

```text
monitortrades:binance:TAOUSDC:cycle-18:hard-tp:SELL
assetguardian:binance:TAOUSDC:window-20260826:tier-2:BUY
spot-dca:kraken:TAOUSD:cycle-4:DCA-1:BUY
```

Retries of one intent reuse the same `intent_id` and `client_order_id`. Independent
strategies, accounts, cycles, tiers, and sides remain distinct even when they target
the same symbol.

## Required record fields

Before enabling semantic deduplication, every producer must publish:

- `intent_id`, `origin`, `venue/account`, `symbol`, `side`, and `kind`;
- requested quantity, order type, requested/reference price, and budget boundary;
- strategy cycle/campaign/tier and an explicit validity/revalidation policy;
- deterministic `client_order_id` where the venue supports it;
- retry policy for absent, ambiguous, rejected, canceled, and expired states;
- terminal policy defining when fills are applied and when the strategy acknowledges
  completion.

## Lifecycle rule

The common monitor may reconcile persisted intent, venue order, status, fills, and
balances. It may retry an absent or ambiguous submit only under the intent's policy.
It must not blindly resubmit an open order or an intentionally canceled/expired order.
The originating strategy remains responsible for revalidating strategy-specific
signals before a new exposure-increasing submit.

Trend deferral is not an attempted submit: it consumes neither attempt count nor TTL.
If a different refusal reason later appears, the normal active TTL begins at that
transition.

## Migration gate

Keep `RETRY_DEDUP=false` until every live producer has a stable `intent_id` and tests
cover response loss, restart after persistence/before submit, partial fill, cancel/fill
races, repeated signal emission, and two independent same-symbol/same-side intents.
Only then replace the legacy boolean with semantic deduplication keyed by `intent_id`.

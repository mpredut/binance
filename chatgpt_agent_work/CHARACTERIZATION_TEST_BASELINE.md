# Financial characterization test baseline

Baseline created: 2026-08-19.

## Purpose

These tests freeze current financial behaviour before the risk/order pipeline is
refactored.  They do not define a new trading feature and do not use credentials,
network calls, live cache files or real exchange orders.

The relevant question on a failure is: **did the refactor intentionally change the
financial result?**  A changed result must be reviewed rather than accepted as a
mechanical refactor.

## Protected outputs

- whether an order is emitted;
- BUY versus SELL;
- final price and quantity;
- full versus fractional exit;
- force/pair/safeback flags;
- retry enqueue/dequeue behaviour;
- cooldown and concurrency behaviour;
- persistence across trailing-stop restart.

## Baseline suite

```bash
.venv/bin/python -m pytest -q \
  tests/test_financial_characterization_monitortrades.py \
  tests/test_instrument_guards.py \
  tests/test_order_retry.py \
  tests/test_order_retry_worker.py \
  tests/test_binance_mechanics.py \
  tests/test_trade_cooldown.py \
  tests/test_trailing_stop.py
```

Result on 2026-08-19:

```text
89 passed
```

## New monitortrades scenarios

`tests/test_financial_characterization_monitortrades.py` records:

1. gross weighted BUY/SELL averages and net position quantity;
2. hard-TP fraction, `force=True`, and early return after a successful hard-TP;
3. normal take-profit selling the entire free balance;
4. UP trend blocking normal take-profit;
5. loss exit selling the entire free balance with `pair=True`;
6. buyback quantity calculated as `buy_budget / current_price`;
7. buyback price using the configured fixed offset;
8. maximum-exposure gate based on `free_qty * current_price`;
9. average BUY reference changing the sell decision;
10. venue minimum-quantity interaction with hard-TP.

## Characterized surprising behaviour

When the fractional hard-TP quantity is below the venue minimum, `_place_guarded`
skips that fractional order and returns `False`.  The tick then continues into the
normal take-profit branch.  If that branch is eligible, it sells the **entire free
balance**.

This baseline intentionally records that behaviour.  Whether it is desirable must
be decided as a separate product/risk change.  It must not be silently changed by
the structural refactor.

## Full-suite state at baseline

Full command:

```bash
.venv/bin/python -m pytest -q
```

Observed result:

```text
633 passed, 4 skipped, 18 failed, 75 subtests passed
```

The 18 existing failures are outside the new characterization file and are grouped
in the Kraken subsystem:

- three Kraken trailing tests still expect the previous 15% HYPE threshold, while
  current production code uses 18%;
- five Kraken xStock adoption tests expect `_maybe_adopt` and `adopted` state that
  are absent from the current strategy implementation;
- ten Kraken re-entry tests construct `StratParams` with removed adoption/re-entry
  fields and therefore fail during setup.

These failures represent code/test drift that existed when the baseline was added.
Do not change production financial behaviour merely to make those stale expectations
pass.  Reconcile them in a separate Kraken characterization task.

## Safety rules for future refactors

1. Run the 89-test baseline before and after every risk/execution refactor.
2. No baseline test may access a real API or runtime state file.
3. A changed assertion requires an explicit financial-behaviour decision.
4. Bug fixes get a separate desired-behaviour test and commit.
5. Preserve the old characterization until the behaviour change is reviewed.

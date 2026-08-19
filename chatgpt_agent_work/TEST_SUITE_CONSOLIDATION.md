# Test suite consolidation audit

Audit and refactor date: 2026-08-19.

## Goal

Reduce repeated test code and excessive top-level fragmentation without weakening
financial, lifecycle, persistence, concurrency, venue-specific or failure-path
coverage.

The consolidation rule was deliberately narrow:

- combine cases only when they use the same setup, production call and assertion
  shape;
- keep every case identifiable through `unittest.subTest`;
- keep financial order paths, concurrency, persistence and multi-step state machines
  as separate top-level tests;
- do not merge tests merely because their names are similar across venues.

## Before and after

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Collected pytest nodes | 665 | 577 | -88 (-13.2%) |
| Passing top-level tests | 661 | 573 | -88 |
| Skipped tests | 4 | 4 | unchanged |
| Passing subtests | 75 | 185 | +110 |
| Tracked test-source lines | 11,620 | 11,398 | -222 |
| `test_tradeall_pricewindow.py` nodes | 120 | 77 | -43 |
| `test_cache_manager_full.py` nodes | 113 | 94 | -19 |

The subtest increase is intentional: repeated methods became table-driven cases, so
pytest still reports the exact case that fails. The 89-test financial characterization
baseline remains unchanged and passes independently.

## Refactored groups

Twelve files were consolidated:

- `tests/test_tradeall_pricewindow.py`: trend direction, gradients, sample-rate
  boundaries, neutral states, cooldown timelines, frequency measurements, cache
  subscriptions, analyzer input/output matrices and coordinator cache lifecycle;
- `tests/test_cache_manager_full.py`: validation matrices, invalid asset values,
  price-read APIs, subscriber lifecycle, WS health transitions and factory contracts;
- configuration/parser cases in `test_alerts_config.py`,
  `test_instruments_conf_parsing.py` and `test_backtest_ranges.py`;
- classification and filtering cases in `test_bapi_ws.py`,
  `test_anomaly_dev_exclusion.py` and `test_rtrade_trend_filter.py`;
- numerical cases in `test_kraken_add_order_rounding.py`,
  `test_kraken_min_order_qty.py`, `test_shadow_signals.py` and
  `test_trend_stats.py`.

No live trading, strategy, risk or order code was changed.

## Duplicate analysis

AST-normalized comparison found only three exact-body groups in the automated suite.
They remain separate intentionally because each is a contract check on a different
concrete cache implementation:

1. `append_mode=True` on Trade, SparsePrice, Price24 and AssetValue managers;
2. `rebuild_fetchtime_times() is None` on Trade and Order managers;
3. empty-cache rebuild behavior on SparsePrice and LongTrend managers.

Combining these would couple unrelated manager construction and make a component
regression less direct to diagnose. Similar trailing-stop names in Binance and Kraken
were also retained: they exercise different venue clients, thresholds and order
mechanics, so they are not duplicates.

## Test-isolation findings

The audit also found two order-dependent import problems:

- `test_tradeall_pricewindow.py` mocked only legacy top-level Binance imports, so an
  isolated run could initialize the real package client;
- package-level fakes initially leaked through `sys.modules` during full-suite
  collection and changed the daily-limit tests.

The test now installs its import fakes only while importing the subject modules,
then restores both `sys.modules` and the `binance_api` package attributes. Combined
and isolated runs both pass. Cache-manager factory tests also mock the actual package
functions used at runtime, preventing accidental API calls.

## Files named like tests but not collected

The repository contains manual/live diagnostics that pytest does not collect:

- `tests/ws/testWS.py` through `tests/ws/testWS8.py`;
- `212trading/test_sl_rebuy.py` (a standalone `main()` scenario);
- `altele/test.py` and `altele/test2.py` (offline research scripts).

They are excluded from the 577-node count. The nine WS files contain historical live
API experiments and should eventually move to `offline/manual/ws/` or be replaced by
one explicit CLI diagnostic. They were not silently merged because some variants use
different Binance websocket protocols and real credentials.

## Verification

Financial baseline:

```text
89 passed
```

Complete suite:

```text
573 passed, 4 skipped, 185 subtests passed
```

Remaining non-failing debt: 13 dependency/process warnings and background cache/trend
threads that can emit logs briefly after pytest shutdown. Thread ownership and teardown
should be handled as a separate infrastructure refactor.

# Verification status

Last updated: 2026-08-23. This is an observational snapshot, not proof of fills or
future profitability.

## Automated gates

- Complete suite: `956 passed`, `296 subtests passed` after tradeall hardening.
- Monitortrades characterization and cooldown gates: `38 passed`.
- Import inventory: `16/16 OK` for active/common modules.
- Instrument/provider routing: PASS. Private Binance balance checks are explicitly
  skipped on local WSL when credentials are unavailable; `None` is not treated as a
  real zero balance.
- Account-facade parity: confirmed differences are zero. Private comparisons are
  inconclusive and skipped when the safe facade reports unavailable account data.

The remaining three test warnings come from the installed `python-binance` dependency
importing deprecated `websockets` compatibility APIs. They are not suppressed because
keeping them visible makes the dependency upgrade debt explicit. The previous Python
3.14 fork warnings were removed by running the cross-process cooldown test with
`spawn`, without weakening its file-lock assertion.

## Order ownership inventory

The static inventory reports three informational overlaps, all within an explicit
coordination domain:

- Binance `BTCUSDC`: monitortrades and tradeall are primary owners; assetguardian and
  trailing are additional protective/portfolio owners.
- Binance `TAOUSDC`: monitortrades, rtrade and tradeall are primary owners;
  assetguardian and trailing are additional owners.
- Kraken `HYPEUSD`: monitortrades is primary and trailing is protective.

These are not currently classified as conflicts by the inventory. They must be
rechecked before changing execution ownership, bypass policy or a bot's live symbol
set.

## Production snapshot

PROD was deployed at `main@42ee397`; `binance.service`, `monitortrades.py` and
`rtrade.py` were active after restart, the checkout was clean, and the inspected recent
logs contained no traceback, 429/418 or timeout errors.

## Deferred because they are not quick, behavior-neutral changes

- durable hard-TP intent/cooldown reconciliation;
- fleet-wide typed order outcomes and unknown-submit recovery;
- retry/cancel-replace lifecycle redesign;
- wiring common market-regime decisions into Kraken/Hyperliquid live policies;
- strategy promotion before sufficient forward divergences and real fill calibration.

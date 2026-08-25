# binance — multi-venue trading system

The repository contains the live trading and monitoring system for Binance,
Kraken and Trading 212, adapters for Hyperliquid, plus the separate replay,
backtest and validation environment. The repository name is historic: the
architecture is no longer exclusively tied to Binance.

> **Attention:** the code can place real orders. Do not manually start live
> entrypoints and do not run the same fleet in two places with the same keys.
> Use the manifest and operational runbook.

## Current architecture

```text
                    config + instruments.conf
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
   Binance fleet       independent bots    offline research
  (flota_start.sh)      (bots_start.sh)    (offline/, research/)
          │                   │                   │
 tradeall / rtrade       Kraken / T212       replay + backtest
 monitortrades           trailing stops      without live keys
 priceAnalysis           Hyperliquid adapter       │
 cacheManager                  │                   │
          └──────────────┬─────┴───────────────────┘
                         │
             strategies/ + providers/
                         │
        guards → audit → submit/status/cancel
                         │
               persistent state and logs
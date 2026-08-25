markdown
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
1. Orchestration and supervision
procs.conf is the single inventory of processes. Each entry has a role:

fleet — processes coordinated by flota_start.sh, launched from the virtual
environment and supervised by the systemd service's own loop binance;

bot — independent processes launched by bots_start.sh and verified/repaired
by healthcheck.sh --supervise.

healthcheck.sh detects both missing processes and live processes with a frozen
heartbeat. flota_start.sh uses flock, so two fleet instances cannot trade
simultaneously.

2. Main Binance fleet
Process	Responsibility
cacheManager.py	market caches, prices, and shared state
priceAnalysis.py	trend, historical windows, and derived signals
tradeall.py	main accumulation/trading strategy
rtrade.py	reaction pipeline and stabilised concurrent execution
monitortrades.py	generic monitoring via the multi-provider facade
assetguardian.py	asset protections and checks
market_alerts.py	market alerts with cooldown and configurable watchlist
order_retry_worker.py	single consumer of the persistent queue of failed orders
tradeall_observe.py is an auxiliary observation process. The dense archiver
tradeall_price_archiver.py is part of the supervised fleet manifest: on restart
it loads the existing JSONL and continues appending, and on SIGTERM it flushes.
An interruption, however, leaves an unrecoverable gap at the WebSocket resolution,
which is why the watchdog restarts it quickly.

3. Strategies and execution
strategies/spot_dca.py contains the common spot DCA engine for both live and replay;

strategies/state_store.py atomically writes the financial state and treats
corruption or inability to save as fail-closed in live;

providers/market_api.py routes operations by symbol to Binance, Kraken,
Hyperliquid, or Trading 212;

providers/execution_audit.py attaches an intent_id and writes the
submit/status/cancel cycle to logger/execution_audit/, without modifying the
decision;

order_guard.py, order_retry.py, and order_retry_worker.py apply guards,
persistence, and retry reconciliation;

trailing_core.py is the common state machine, and the Binance and Kraken
adapters each keep the specific API, configuration, and state for their own
venue.

Providers unify the mechanics of venue access, not financial strategies. T212 and
Hyperliquid keep their own logic where the execution model differs.

4. Independent bots
kraken/ — common fills cache, spot bot, xStocks watcher, and trailing stop;

212trading/ — a single t212_bot.py process, with configurable profiles and
persistent state for orders, partial fills, cancellation, and repricing;

binance_api/trailing_stop.py — trailing/re-buy circuit breaker for Binance;

hyperliquid/ — HYPE client and provider. dn_bot is disabled in
procs.conf; hl_dca_bot is configured outside the manifest with REAL gates
active, but is stopped until the DCA reserve is funded. Snapshot from August 21:
~1,024 USDC available, zero orders; the 1,000/600 profile requires at least
7,000 USDC.

Kraken uses a separate namespace for its transaction cache. Kraken processes that
send private requests must respect the key/nonce policy documented in
docs/ARCHITECTURE.md.

5. Data, state, and configuration
.env, *.env files, and keys are local and not committed;

.env.example and configurations without secrets describe the configuration
contract;

cachedb/ contains caches and persistent queues;

logs/ and logger/ directories contain heartbeats, audit trails, and runtime
results;

instruments.conf defines the instruments and their provider;

procs.conf defines only the processes and their supervision mode.

Do not delete state files for a "clean restart": the bot may lose ownership of a
position or duplicate an existing entry.

6. Live versus offline
offline/ and research/ are the area for replay, simulations, backtesting, and
candidate promotion. The financial engine can be shared with live, but the
offline entrypoint receives a controlled executor and must not have access to
private trading capabilities. Promotion to production requires the tests and
active evidence described in the validation documentation.

Quick operations
Run the commands from the repository root:

Action	Command
Read-only status	./healthcheck.sh --check
Supervise bot processes	./healthcheck.sh --supervise
Start/restart fleet	sudo systemctl restart binance
Start independent bots	./bots_start.sh
Check ownership	.venv/bin/python verify_tools/ownership_inventory.py --running
Portfolio snapshot	.venv/bin/python verify_tools/portfolio_snapshot.py
Controlled deployment	./deploy_providers.sh
Backup secrets	./backup_secrets.sh / ./backup_remote.sh
Restore server	./restore.sh <secret_folder>
After any change to the manifest or configuration:

bash
./healthcheck.sh --check
git status --short
Do not use pkill -f manually without checking the pattern; the operational
scripts have the necessary ordering and exceptions for restart.

Repository map
Path	Role
providers/	market data/execution contracts and adapters
strategies/	reusable financial logic and state store
binance_api/	Binance client and trailing adapter
kraken/	Kraken integration and processes
212trading/	Trading 212 engine and integration
hyperliquid/	Hyperliquid integration; DN disabled in production
forecast/	trend and survival estimates
verify_tools/	health, ownership, snapshot, and operational validations
offline/	runners, simulations, and replay isolated from live
research/	experiments and results before promotion
tests/	unit tests, characterisation, and regression
docs/	architecture, strategy, operations, and disaster recovery
systemd/	units for server and VPN
Documentation
docs/README.md — documentation index;

docs/OPERATIONS.md — runbook, reboot, and diagnostics;

docs/ARCHITECTURE.md — contracts, providers, and ownership;

docs/STRATEGY.md — financial rules;

docs/DISASTER_RECOVERY.md — backup and restoration;

kraken/README.md and
hyperliquid/README.md — component-specific details.
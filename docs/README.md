# docs/ — operational documentation (centralised)

Cross-cutting documentation for the trading system. Component READMEs live next to
their code (a deliberate convention — they are linked below).

## Operational / runbook
- [OPERATIONS.md](OPERATIONS.md) — how it works (architecture, manifest, supervision) plus
  **pitfalls and lessons** (fd lock leak, hang vs crash, DN co-mingling, the execute bit, quoting) and diagnostics.
- [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) — full rebuild on a new VM (the DR seed,
  secret backups, restore.sh), periodic backups, what is and is not in git.
- [VERIFICATION_STATUS.md](VERIFICATION_STATUS.md) — the last reproducible verification,
  the limits of the local environment, and the intentional ownership overlaps.

## Design and strategy (the durable whys)
- [STRATEGY.md](STRATEGY.md) — the trading logic: trend detection (+48h lag, survival
  curve, lindy plateau), the profit guard, trailing re-buy, the T212 profit guard/ladder, xStocks.
- [RTRADE.md](RTRADE.md) — the rtrade policy, the BUY/SELL cycle, financial evaluation,
  dynamic stops, ownership and order recovery.
- [TRADEALL.md](TRADEALL.md) — the signal pipeline, the Kalman gate, per-trend limits,
  retry ownership and the restart risks still open.
- [ASSETGUARDIAN.md](ASSETGUARDIAN.md) — signals on portfolio value, the MIN/MAX
  baseline, guarded execution, and the concentration risk of drawdown buying.
- [ARCHITECTURE.md](ARCHITECTURE.md) — the `providers/market_api` facade plus providers, HYPE on HL,
  multi-process Kraken (a shared cacheManager), trailing stop (shared core plus adapters).
- [ORDER_RETRY_ARCHITECTURE.md](ORDER_RETRY_ARCHITECTURE.md) — the outbox versus the
  lifecycle owned by the strategy, the cache threads, the JSON state and order reconciliation.
- [ORDER_LIFECYCLE_CENTRALIZATION.md](ORDER_LIFECYCLE_CENTRALIZATION.md) — the limits of
  the shared refactor and an inventory of the migrated paths.
- [ORDER_INTENT_DEDUP_DESIGN.md](ORDER_INTENT_DEDUP_DESIGN.md) — the saved analysis of
  the semantic keys and why `RETRY_DEDUP=false` is still in force.

## Component READMEs (next to the code)
- [../hyperliquid/README.md](../hyperliquid/README.md) — Hyperliquid: spot stopped for insufficient capital, runtime gates, isolated state and the long-term shadow candidate.
- [../kraken/README.md](../kraken/README.md) — Kraken: the bots (HYPE, xStock, trailing) plus the cachemanager.

## Quick maps (where things are)
- **Single process manifest**: `procs.conf` (repository root) — read by `healthcheck.sh`, `flota_start.sh` and `bots_start.sh`.
- **Supervision**: `healthcheck.sh` — `--supervise` (restarts dead and frozen processes), `--alert`, `--check` (read-only).
- **Startup**: `flota_start.sh` (the fleet, under systemd `binance`), `bots_start.sh` (the bots).
- **Backup/DR**: `backup_secrets.sh` (local), `backup_remote.sh` (encrypted Storj), `restore.sh` (rebuild), `systemd/crontab.prod.txt`, `requirements.txt`, `systemd/install_prod.sh`.

# Runtime state ownership and locking

Atomic publication prevents partial files; it does not serialize a complete
read-modify-write transaction. This inventory records the separate ownership rule.

| State | Writer ownership | Serialization | Assessment |
|---|---|---|---|
| Strategy state (`strategies/state_store.py`) | one bot per venue/instrument | launcher `single_instance` | safe for normal fleet launch |
| AssetGuardian tranches | AssetGuardian operations | `FileLock` around read and write | cross-process serialized |
| rtrade pair rounds | rtrade operations | `FileLock` around mutation | cross-process serialized |
| retry outbox | all execution processes | dedicated queue lock plus file/directory sync | intentionally specialized |
| Hyperliquid delta-neutral | one process per coin | state-specific `flock` | cross-process serialized |
| Binance/Kraken trailing | daemon plus optional one-shot command | daemon-only `single_instance` | one-shot `--once` can race the daemon |
| Kraken xStock watcher | daemon plus optional one-shot command | daemon-only `single_instance` | `--once` can race the daemon |
| Hyperliquid perpetual strategy | `hl_bot.py` | no launcher or state-file lock | duplicate manual launch can race |
| Kraken strategy | one process per pair | pair-specific `single_instance` | normal loop safe; one-shot strategy command needs review |
| Cache managers | one fleet process plus internal threads | fleet ownership and in-process locks | safe while deployment preserves a single process |
| Shadow/monitoring state | cron-owned | atomic last-writer-wins | acceptable; no financial authorization |

## Follow-up decisions

Do not add a lock inside `state_io`: doing so would protect only one write, not the
read-modify-write transaction. The owning component must hold a lock across its full
mutation. The actionable gaps are:

1. decide whether trailing and xStock `--once` should acquire the daemon lock and fail
   when the daemon is active, or become strictly read-only diagnostics;
2. add a per-instrument lock to `hyperliquid/hl_bot.py` before allowing manual parallel
   launches;
3. decide whether Kraken `--test-strategy` may mutate the live pair state while its
   daemon is active;
4. retain deployment tests that guarantee one cache-manager process.

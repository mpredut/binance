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
| Binance/Kraken trailing | daemon plus optional one-shot command | daemon and `--once` share `single_instance`; status is read-only | serialized |
| Kraken xStock watcher | daemon plus optional one-shot command | daemon and `--once` share `single_instance`; trial state is isolated | serialized |
| Hyperliquid perpetual strategy | one process per coin | coin-specific `single_instance`, including test strategy | serialized through launcher |
| Kraken strategy | one process per pair | pair-specific `single_instance`, including test strategy | serialized through launcher |
| Cache managers | one fleet process plus internal threads | fleet ownership and in-process locks | safe while deployment preserves a single process |
| Shadow/monitoring state | cron-owned | atomic last-writer-wins | acceptable; no financial authorization |

## Follow-up decisions

Do not add a lock inside `state_io`: doing so would protect only one write, not the
read-modify-write transaction. The owning component must hold a lock across its full
mutation. The formerly identified one-shot and strategy-launch gaps now share their
daemon locks. Retain deployment tests that guarantee one cache-manager process. Direct
library construction outside these launchers remains the caller's responsibility.

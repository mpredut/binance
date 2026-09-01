# Storage and retention policy

The repository uses bounded, explicit policies for data that grows over time.
Configuration is mandatory and versioned; missing values fail during component startup.

## Data classes

- Operational state and active financial intents use atomic snapshots and are never
  pruned by generic retention.
- Binance fills, orders and portfolio-value history use incremental JSONL files. On the
  first upgraded start, the previous full JSON file is imported without deleting it.
- Sparse and dense price histories append compact JSONL records, periodically deduplicate
  and prune by timestamp, and rotate above the configured size.
- Rotated cache archives are gzip-compressed atomically. A bounded number is retained.
- Application logs buffer a small batch in memory, rotate by date/size, compress after the
  configured age and expire after the configured retention period.

## Authorities

- `cachemanager_config.env` owns cache retention, rotation, resynchronization and dense
  archive sampling/flush settings.
- `logger_config.env` owns log volume, batching, rotation, compression and deletion.

The active cache remains plain JSONL for streaming recovery and append throughput.
Compression is limited to immutable rotated archives so a process crash cannot corrupt an
actively appended gzip stream.

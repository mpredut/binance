# binance — sistem de trading multi-platformă

Flotă Binance (DCA + trend) + boți Hyperliquid (delta-neutral), Kraken și T212, cu
supraveghere unificată și backup / disaster-recovery.

## 📚 Documentație
- **[docs/](docs/README.md)** — index documentație (operațional + hartă „unde e ce").
- **[docs/DISASTER_RECOVERY.md](docs/DISASTER_RECOVERY.md)** — refacere completă pe mașină nouă.
- README de componentă: [hyperliquid/](hyperliquid/README.md) · [kraken/](kraken/README.md).

## ⚙ Operare rapidă
| Acțiune | Comandă |
|---|---|
| Stare procese | `./healthcheck.sh --check` |
| Supraveghere (cron */5) | `./healthcheck.sh --supervise` — repornește morți + înghețați |
| Pornire flotă / boți | `flota_start.sh` (systemd `binance`) · `bots_start.sh` |
| **Deploy cod** | `./deploy_providers.sh` — pull + gate import + restart flotă + verificare |
| Backup secrete | `./backup_secrets.sh` (local) · `./backup_remote.sh` (Storj criptat) |
| Refacere | `./restore.sh <folder_secrete>` — vezi docs/DISASTER_RECOVERY.md |

**Sursa unică de procese:** `procs.conf` — citită de `healthcheck.sh`, `flota_start.sh`,
`bots_start.sh`, `deploy_providers.sh` (adaugi/scoți un proces → un singur loc).

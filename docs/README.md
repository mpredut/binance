# docs/ — documentație operațională (centralizată)

Documentația transversală a sistemului de trading. READMI-urile de componentă stau
lângă codul lor (convenție — sunt linkate mai jos).

## Operațional / runbook
- [OPERATIONS.md](OPERATIONS.md) — cum funcționează (arhitectură, manifest, supraveghere) +
  **capcane & lecții** (lock-leak fd, hang vs crash, co-mingling DN, bit execuție, quoting) + diagnostic.
- [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) — refacere completă pe VM nou (sămânța DR,
  backup secrete, restore.sh), backup periodic, ce e/nu e în git.

## Design & strategie (de ce-uri durabile)
- [STRATEGY.md](STRATEGY.md) — logica de trading: detecție trend (lag +48h, curbă de
  supraviețuire, lindy plateau), garda de profit, trailing re-buy, T212 profit-guard/ladder, xStocks.
- [ARCHITECTURE.md](ARCHITECTURE.md) — facadă `providers/market_api` + provideri, HYPE pe HL,
  Kraken multi-proces (cacheManager partajat).

## README de componentă (lângă cod)
- [../hyperliquid/README.md](../hyperliquid/README.md) — Hyperliquid: delta-neutral (dn_bot) + provider HYPE.
- [../kraken/README.md](../kraken/README.md) — Kraken: boți (HYPE, xStock, trailing) + cachemanager.

## Hărți rapide (unde e ce)
- **Manifest unic procese**: `procs.conf` (rădăcină) — citit de `healthcheck.sh`, `flota_start.sh`, `bots_start.sh`.
- **Supraveghere**: `healthcheck.sh` — `--supervise` (repornește morți + înghețați), `--alert`, `--check` (read-only).
- **Pornire**: `flota_start.sh` (flota, sub systemd `binance`), `bots_start.sh` (boții).
- **Backup/DR**: `backup_secrets.sh` (local), `backup_remote.sh` (Storj criptat), `restore.sh` (refacere), `crontab.txt`, `requirements.txt`, `systemd/`.

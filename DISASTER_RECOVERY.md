# Disaster Recovery — refacerea completă a serverului de trading

Tot ce e **versionat** (cod, manifest `procs.conf`, unit-uri systemd, `requirements.txt`,
`crontab.txt`, scripturi) vine din git. Singurul lucru care **NU e în git** (și nu trebuie
să fie) sunt **secretele** — le ții într-un backup separat, off-machine.

## Ce e în git vs ce ții tu (off-machine)

| În git (auto din `git clone`) | NU în git — backup separat |
|---|---|
| cod + `procs.conf` + scripturi (flota_start, bots_start, healthcheck, restore.sh) | `.env` (root, hyperliquid, kraken, 212trading) |
| `systemd/*.service` (binance, pia) | `keys/apikeys.py` (chei Binance) |
| `requirements.txt` (dependințe venv) | `keys/ed25519_*.pem` (chei Kraken) |
| `crontab.txt` (toate cronurile) | (opțional) stare boți: `.state_*.json`, `cachedb/` |

⚠ **`hyperliquid/.env` conține cheia agent-wallet HL** — pierderea ei = nu mai poți semna
ordine HL. Backup-ul de secrete e CRITIC.

## Refacere pe o mașină nouă (Ubuntu) — pașii

```bash
# 0. dependinte sistem (o data)
sudo apt update && sudo apt install -y git python3 python3-venv

# 1. cod
git clone git@github.com:mpredut/binance.git ~/binance
cd ~/binance

# 2. copiaza folderul de SECRETE de pe backup (USB/cloud privat/alta masina)
#    structura lui oglindeste repo-ul: .env, hyperliquid/.env, keys/, (state/)
#    ex:  scp -r user@backup:~/binance-secrets-backup /tmp/

# 3. O SINGURA COMANDA — reface tot (secrete + venv + systemd + cron):
./restore.sh /tmp/binance-secrets-backup

# 4. PIA/VPN (o data): instaleaza clientul PIA + login, apoi:
sudo systemctl start pia binance

# 5. verifica
./healthcheck.sh --check        # toate procesele = 'ok'
```

`restore.sh` face: restore secrete → creează `myenv` + `pip install -r requirements.txt`
→ instalează unit-urile systemd (enable) → instalează crontab-ul. După `systemctl start`,
**flota** pornește prin systemd, iar **boții** prin cron `healthcheck.sh --supervise` (≤5 min).

## Cum (re)faci backup-ul de secrete

Rulează pe mașina vie (creează folder + tar, fără să atingă git):

```bash
~/binance/backup_secrets.sh            # -> ~/binance-secrets-backup/ + .tar.gz
# apoi copiaza tarball-ul OFF-machine (USB, cloud privat, alta masina)
```

Reține: secretele nu intră NICIODATĂ în git (sunt în `.gitignore`). Backup-ul e
responsabilitatea ta să-l ții în siguranță și off-machine.

## La reboot (fără refacere) — totul revine singur
- `binance.service` e `enabled` → systemd pornește flota după VPN.
- crontab persistă pe disc → `healthcheck --supervise` (cron */5) pornește boții în ≤5 min.
- Nimic de făcut manual.

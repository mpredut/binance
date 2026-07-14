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

## ⚠ Cum ajung secretele pe un VM GOL? (chicken-and-egg)

Un VM nou **nu are nicio cheie** și NU „trage de pe WSL" singur. Restaurarea o **inițiezi TU**
(de pe dev box, sau de pe VM cu sămânța DR). Direcția: ori **VM-ul descarcă din Storj**, ori
**împingi tu** backup-ul pe VM. Niciodată „VM-ul pull din WSL".

**🔑 SĂMÂNȚA DR** — ține astea 3 SEPARAT (password manager / hârtie), NU doar în backup:
1. URL repo git (`git@github.com:mpredut/binance.git`) + acces GitHub (cheie SSH sau token HTTPS)
2. **Storj access grant**
3. **parola rclone crypt** (decriptarea backup-ului)

Cu cele 3, un VM complet gol se reface singur. (Dacă le pui DOAR în backup → chicken-and-egg:
ai nevoie de ele ca să descarci backup-ul care le conține.)

## Refacere pe o mașină nouă (Ubuntu) — pașii

```bash
# 0. dependinte
sudo apt update && sudo apt install -y git python3 python3-venv curl unzip

# 1. cod  (HTTPS+token daca n-ai cheia GitHub pe VM; sau adaugi cheia)
git clone git@github.com:mpredut/binance.git ~/binance && cd ~/binance

# 2. ADU backup-ul de secrete pe VM — alege A sau B:
#   (A) STORJ (recomandat, fara dev box): configureaza rclone cu access grant + crypt
#       password (din samanta DR), apoi descarca + decripteaza:
#         ~/bin/rclone copyto storj-crypt:binance-secrets-backup.tar.gz ~/bk.tar.gz
#         mkdir bk && tar xzf ~/bk.tar.gz -C bk
#   (B) PUSH de pe dev box (interimar): pe DEV BOX ruleaza
#         scp ~/binance-secrets-backup.tar.gz user@VM_NOU:/tmp/
#       apoi pe VM: mkdir bk && tar xzf /tmp/binance-secrets-backup.tar.gz -C bk

# 3. O COMANDA — reface tot (secrete + venv + systemd + cron):
./restore.sh bk/binance-secrets-backup

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

## Copie locală pe WSL (interimar, până la Storj) — task Windows
Serverul reface backup-ul zilnic (cron 03:30, `backup_secrets.sh`) și păstrează **istoric:
ultimele 7 tarball-uri datate** (`binance-secrets-backup-YYYYMMDD.tar.gz`) pe lângă calea
stabilă `binance-secrets-backup.tar.gz` (latest) — o corupere care intră în backup NU mai
suprascrie unica copie bună. Un task Windows trage latest-ul la 04:00 (WSL nu ajunge la
server — doar Windows): descarcă întâi **local** (`%USERPROFILE%\binance-secrets-backup.tar.gz`,
merge și cu WSL oprit), apoi copiază și în WSL. Scriptul e versionat:
[`../windows/pull-binance-backup.ps1`](../windows/pull-binance-backup.ps1) (keyless).

Setare pe un Windows nou (o dată):
```powershell
# 1. cheie SSH (fara parola, pt automatizare) + adaug publica pe server
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_binance" -N '""' -C binance-backup-pull -q
# adauga continutul id_binance.pub in ~/.ssh/authorized_keys pe server (o data, cu parola)
# 2. copiaza scriptul din repo pe Windows (ajusteaza caile daca difera)
copy <repo>\windows\pull-binance-backup.ps1 C:\Users\<user>\pull-binance-backup.ps1
# 3. task zilnic 04:00 cu recuperare daca PC-ul era oprit
$a=New-ScheduledTaskAction -Execute powershell.exe -Argument '-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\<user>\pull-binance-backup.ps1'
$t=New-ScheduledTaskTrigger -Daily -At 4:00am
$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName BinanceBackupPull -Action $a -Trigger $t -Settings $s -Force
```
Cheia PRIVATĂ `id_binance` NU intră în git (o pui în backup-ul de secrete sau o regenerezi +
re-adaugi publica pe server). Când pornește Storj, task-ul ăsta devine opțional.

## La reboot (fără refacere) — totul revine singur
- `binance.service` e `enabled` → systemd pornește flota după VPN.
- crontab persistă pe disc → `healthcheck --supervise` (cron */5) pornește boții în ≤5 min.
- Nimic de făcut manual.

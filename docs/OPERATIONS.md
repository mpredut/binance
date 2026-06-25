# OPERATIONS — cum funcționează + capcane (runbook)

„De ce"-ul sistemului și capcanele care nu se văd din cod. Pentru refacere completă vezi
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).

## Arhitectură pe scurt
- **Flota Binance** (7 procese): `cacheManager`, `assetguardian`, `priceAnalysis`,
  `tradeall`, `monitortrades`, `rtrade`, `market_alerts`. Pornite + supravegheate de
  `flota_start.sh`, care rulează sub **systemd `binance`** (enabled, `Restart=always`).
- **Boți** (separați de flotă): `dn_bot` (delta-neutral HL) + `dn_bot --watch` (monitor
  read-only), `kraken_cachemanager`, `kraken_bot`, `kraken_xstock_watch`, `t212_bot`,
  `kraken/trailing_stop`, `binance_api/trailing_stop`. Porniți de `bots_start.sh`,
  supravegheați de `healthcheck.sh --supervise` (cron */5).
- **Facadă market/cont**: `providers/market_api.py` rutează pe symbol către
  `BinanceProvider` / `HyperliquidProvider` / `kraken` / `t212`. `monitortrades` o folosește.

## Sursa unică de procese: `procs.conf`
Format: `pat | dir | start_cmd | label | hb_log | hb_stale_s | role` (`role=bot|fleet`).
Citită de **toate**: `healthcheck.sh`, `flota_start.sh`, `bots_start.sh`, `deploy_providers.sh`.
**Adaugi/scoți/modifici un proces → editezi DOAR `procs.conf`.**

## Supraveghere — `healthcheck.sh`
- `--check` — preview READ-ONLY (ce ar face, fără să atingă nimic). Sigur oricând.
- `--supervise` (cron */5) — repornește `role=bot` morți SAU înghețați; `role=fleet` =
  doar alertă (o ține flota_start). Backoff: max 3 reporniri/30 min, apoi crash-loop alert.
- `--alert` — doar alertă, fără restart.
- **Detecție dublă:** absență (`pgrep`) **și HANG** (proces viu dar `hb_log` nescris de
  `hb_stale_s`). Heartbeat activat pt `dn_bot`/`dn_watch` (600s); restul = doar prezență.

## Pornire / deploy / backup
- **Pornire:** `flota_start.sh` (flotă, systemd) · `bots_start.sh` (boți).
- **Deploy cod:** `deploy_providers.sh` — `git pull` → **gate de import** (nu repornește
  dacă facada nu se încarcă) → restart flotă → verificare.
- **Backup/DR:** `backup_secrets.sh` (local, auto din `git ls-files`), `backup_remote.sh`
  (Storj criptat), `restore.sh`. Detalii în DISASTER_RECOVERY.md.

## La REBOOT — totul revine singur
- systemd `binance` (enabled) → `flota_start` → flota (după VPN/pia).
- crontab persistă → `healthcheck --supervise` (*/5) pornește boții în ≤5 min.
- Nimic manual.

## ⚠ CAPCANE & LECȚII (citește înainte să modifici)

### 1. Scurgere de lock prin moștenire de fd (supervizor blocat „în tăcere")
`flota_start.sh` (`exec 9>flota_start.lock`) și `healthcheck --supervise`
(`exec 8>/tmp/binance_supervise.lock`) folosesc `flock`. Dacă pornesc un copil cu
`nohup … &`, copilul **moștenește fd-ul lock-ului** → ține lock-ul deschis după ce
scriptul iese → următoarea rulare dă „**deja ruleaza**" la infinit = supraveghere
**dezactivată silențios**.
- **Fix (aplicat):** `8>&-` / `9>&-` la spawn (copilul nu mai moștenește fd-ul).
- **Diagnostic:** `lsof /tmp/binance_supervise.lock` (sau `flota_start.lock`) → PID cu `8w`/`9w`.
- **Deblocare imediată:** `rm /tmp/binance_supervise.lock` (următoarea rulare ia inode nou).

### 2. Hang ≠ crash (dn_bot poate îngheța viu)
`dn_bot` poate îngheța silențios (proces viu, fără tick). `pgrep` nu-l prinde →
de-aia există **heartbeat pe mtime-ul logului** (`hb_log`/`hb_stale_s` în `procs.conf`).

### 3. ⚠ Co-mingling SPOT cu DN-ul (HYPE)
Pe Hyperliquid soldul **spot e unul singur** pe wallet. Piciorul LONG spot al botului
delta-neutral și HYPE-ul „monitortrades" sunt în **același sold**. Un SELL real de „tot
ce e disponibil" ar **desface hedge-ul DN**. De-aia `monitortrades` pe HYPE rămâne cu
`HL_LIVE_ORDERS` **off** (DRY) până se separă pozițiile (sub-cont/wallet sau tagging).

### 4. Bitul de execuție se pierde la editări din Windows
Editarea unui `.sh` din Windows/UNC îl resetează la `644` → cron-ul `./script.sh` dă
„Permission denied". **Fix:** `chmod +x x.sh && git update-index --chmod=+x x.sh`.

### 5. `pkill -f` se poate prinde pe SINE
`pkill -f flota_start.sh` rulat dintr-o comandă al cărei string CONȚINE pattern-ul își
omoară propriul shell. **Folosește scripturi-fișier sau PID-uri**, nu pattern inline.

### 6. WSL NU ajunge la server
Din WSL, `192.168.0.144` face buclă la localhost (rutare VPN). **Doar Windows**
(plink/pscp) ajunge la server. Backup-urile se TRAG de pe Windows în WSL, nu invers.

### 7. Quoting plink → PowerShell → bash e fragil
Evită în comenzi inline: `$( )`, `<`, `|` (alternare în grep), `\"`, `\$`, paranteze în
`echo`. Pune logica într-un **script-fișier** (pscp + rulează) când e nontrivială.

### 8. NU rula flota în 2 locuri pe aceleași chei
Flota pornită SIMULTAN local (WSL `/home/mariusp`) ȘI pe server (`/home/predut`), pe
ACELEAȘI chei API live → **tranzacții dublate** + conflicte de nonce pe Kraken. Rulează
flota într-UN singur loc; pentru test local folosește chei separate / cont demo, sau
oprește serverul întâi. (Garda din `--supervise` refuză pornirea pe `/home/mariusp`, dar
`flota_start`/`bots_start` NU au garda — atenție.)

## Diagnostic rapid
```bash
./healthcheck.sh --check                 # stare toate procesele (read-only)
./healthcheck.sh                         # raport complet (procese + conturi HL/Kraken/T212)
( cd hyperliquid && ./myenv/bin/python dn_bot.py --status )   # delta DN, funding, lichidare
lsof /tmp/binance_supervise.lock         # cine ține lock-ul supervize (scurgere?)
tail -n 5 logs/healthcheck.log           # ce a făcut supervizorul (cron)
```

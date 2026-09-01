# Reconstruire PROD

Folderul reproduce configurația de servicii și cron de pe PROD. Codul vine din
Git; secretele și starea persistentă se restaurează separat cu scripturile de
backup/restore înainte de pornirea flotei.

Ordine recomandată pe o mașină nouă:

1. creează utilizatorul `predut` și clonează repo-ul în `/home/predut/binance`;
2. restaurează secretele și fișierele de stare din backup;
3. instalează dependențele/venv și PIA în aceleași căi;
4. rulează `sudo /home/predut/binance/systemd/install_prod.sh`;
5. verifică `systemctl status binance pia piavpn`, cron-ul și healthcheck-ul.

Cron-ul vine din două fișiere: `crontab.prod.txt` (utilizatorul `predut`) și
`crontab.root.prod.txt` (root). Al doilea există pentru că `pia_selfheal.sh` are
nevoie de `systemctl`/`kill` pe `pia-daemon`, deci nu poate rula ca `predut`.

`PIA.md` documentează VPN-ul: comenzile `piactl`, capcanele care ne-au costat 34
de zile de flotă oprită (`pubip` nu e IP-ul de ieșire, `connect` ignorat în tăcere
fără `background enable`, logout-ul șterge IP-ul dedicat) și cum funcționează
auto-repararea.

`binance.service` ține `flota_start.sh` activ. Flota verifică procesele la 30s,
reia procesele moarte/zombie și aplică `SIGCONT` proceselor oprite. Cron rulează
suplimentar `healthcheck.sh --supervise` la trei minute și alertează pentru
heartbeat stale, inclusiv `logs/rtrade.log`.

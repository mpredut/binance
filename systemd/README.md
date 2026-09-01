# Rebuilding PROD

This folder reproduces the service and cron configuration of the PROD machine. The
code comes from Git; secrets and persistent state are restored separately with the
backup/restore scripts, before the fleet is started.

Recommended order on a fresh machine:

1. create the `predut` user and clone the repository into `/home/predut/binance`;
2. restore the secrets and state files from backup;
3. install the dependencies/venv and PIA under the same paths;
4. run `sudo /home/predut/binance/systemd/install_prod.sh`;
5. check `systemctl status binance pia piavpn`, cron, and the healthcheck.

Cron comes from two files: `crontab.prod.txt` (the `predut` user) and
`crontab.root.prod.txt` (root). The second one exists because `pia_selfheal.sh`
needs `systemctl`/`kill` on `pia-daemon`, so it cannot run as `predut`.

`PIA.md` documents the VPN: the `piactl` commands, the pitfalls that cost us 34 days
of a stopped fleet (`pubip` is not the exit IP, `connect` silently ignored without
`background enable`, logging out deletes the dedicated IP), and how the self-healing
works.

`binance.service` keeps `flota_start.sh` alive. The fleet checks its processes every
30s, restarts dead/zombie ones, and sends `SIGCONT` to stopped ones. Cron
additionally runs `healthcheck.sh --supervise` every three minutes and alerts on
stale heartbeats, including `logs/rtrade.log`.

# Profil DEV / backtest

Configurația autoritativă este `offline/runners/dev_backtest.env`; hostul,
portul, checkout-ul și branch-urile se citesc exclusiv de acolo.

DEV nu are nevoie de cron sau servicii live proprii. PROD orchestrează:

- `refresh_dev.sh` la 30 minute: fast-forward `main` și rsync `cachedb/`;
- `trigger_backtest_dev.sh` la 02:00 și 14:00;
- DEV execută `run_backtest_cycle.sh` și publică pe `backtest-proposals`;
- PROD aplică propunerile cu guardrail-urile din `apply_proposals.py`.

Refacere DEV: clonează `main` în calea configurată, creează `myenv`, instalează
`requirements.txt`, autorizează cheia SSH a PROD și verifică manual
`run_backtest_cycle.sh`. Nu instala `systemd/install_prod.sh` pe DEV și nu copia
cheile exchange PROD; sunt necesare numai datele `cachedb/` sincronizate.

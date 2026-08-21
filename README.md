# binance — sistem de trading multi-venue

Repository-ul conține sistemul live de trading și monitorizare pentru Binance,
Kraken și Trading 212, adaptoare pentru Hyperliquid, plus mediul separat de replay,
backtest și validare. Numele repository-ului este istoric: arhitectura nu mai este
legată exclusiv de Binance.

> **Atenție:** codul poate plasa ordine reale. Nu porni manual entrypoint-uri live
> și nu rula aceeași flotă în două locuri cu aceleași chei. Folosește manifestul și
> runbook-ul operațional.

## Arhitectura actuală

```text
                    config + instruments.conf
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
   flota Binance       boți independenți    cercetare offline
  (flota_start.sh)      (bots_start.sh)      (offline/, research/)
          │                   │                   │
 tradeall / rtrade       Kraken / T212       replay + backtest
 monitortrades           trailing stops      fără chei live
 priceAnalysis           Hyperliquid adapter       │
 cacheManager                  │                   │
          └──────────────┬─────┴───────────────────┘
                         │
             strategies/ + providers/
                         │
        garduri → audit → submit/status/cancel
                         │
               stare și loguri persistente
```

### 1. Orchestrare și supraveghere

`procs.conf` este inventarul unic al proceselor. Fiecare intrare are rolul:

- `fleet` — procesele coordonate de `flota_start.sh`, lansate din mediul virtual
  și supravegheate de bucla proprie a serviciului systemd `binance`;
- `bot` — procese independente lansate de `bots_start.sh` și verificate/reparate
  de `healthcheck.sh --supervise`.

`healthcheck.sh` detectează atât procese absente, cât și procese vii cu heartbeat
înghețat. `flota_start.sh` folosește `flock`, astfel încât două instanțe ale flotei
să nu poată tranzacționa simultan.

### 2. Flota principală Binance

| Proces | Responsabilitate |
|---|---|
| `cacheManager.py` | cache-uri de piață, prețuri și stare partajată |
| `priceAnalysis.py` | trend, ferestre istorice și semnale derivate |
| `tradeall.py` | strategia principală de acumulare/trading |
| `rtrade.py` | pipeline-ul de reacție și execuție concurentă stabilizată |
| `monitortrades.py` | monitorizare generică prin fațada multi-provider |
| `assetguardian.py` | protecții și verificări asupra activelor |
| `market_alerts.py` | alerte de piață cu cooldown și watchlist configurabil |
| `order_retry_worker.py` | consumator unic al cozii persistente de ordine eșuate |

`tradeall_observe.py` și `tradeall_price_archiver.py` sunt procese auxiliare de
observare/arhivare și nu fac parte din manifestul flotei supravegheate.

### 3. Strategii și execuție

- `strategies/spot_dca.py` conține motorul spot DCA comun pentru live și replay;
- `strategies/state_store.py` scrie atomic starea financiară și tratează coruperea
  sau imposibilitatea salvării ca fail-closed în live;
- `providers/market_api.py` rutează operațiile după simbol către Binance, Kraken,
  Hyperliquid sau Trading 212;
- `providers/execution_audit.py` atașează un `intent_id` și scrie ciclul
  submit/status/cancel în `logger/execution_audit/`, fără să modifice decizia;
- `order_guard.py`, `order_retry.py` și `order_retry_worker.py` aplică gardurile,
  persistența și reconcilierea reîncercărilor;
- `trailing_core.py` este mașina de stări comună, iar adaptoarele Binance și Kraken
  păstrează API-ul, configurația și starea specifice fiecărui venue.

Providerii unifică mecanica accesului la venue, nu strategiile financiare. T212 și
Hyperliquid își păstrează logica proprie acolo unde modelul de execuție diferă.

### 4. Boți independenți

- `kraken/` — cache comun de fills, bot spot, watcher xStocks și trailing stop;
- `212trading/` — un proces `t212_bot.py`, cu profiluri configurabile și stare
  persistentă pentru ordine, partial fills, anulare și repricing;
- `binance_api/trailing_stop.py` — disjunctor trailing/re-buy pentru Binance;
- `hyperliquid/` — client și provider HYPE. `dn_bot` este dezactivat în
  `procs.conf`; `hl_dca_bot` rulează manual, în afara manifestului, cu porțile REAL
  active. Redeploy-ul controlat din 21 august a activat starea HL izolată; procesul
  reconciliază ordinul real de intrare fără a mai citi starea PAPER legacy Kraken.

Kraken folosește namespace separat pentru cache-ul de tranzacții. Procesele Kraken
care trimit cereri private trebuie să respecte politica de chei/nonce documentată în
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### 5. Date, stare și configurare

- `.env`, fișierele `*.env` și cheile sunt locale și nu se comit;
- `.env.example` și configurațiile fără secrete descriu contractul de configurare;
- `cachedb/` conține cache-uri și cozi persistente;
- `logs/` și directoarele `logger/` conțin heartbeat-uri, audit și rezultate runtime;
- `instruments.conf` definește instrumentele și providerul lor;
- `procs.conf` definește numai procesele și modul lor de supraveghere.

Nu șterge fișierele de stare pentru un „restart curat”: botul poate pierde ownership-ul
unei poziții sau poate dubla o intrare existentă.

### 6. Live versus offline

`offline/` și `research/` sunt zona pentru replay, simulări, backtest și promovarea
candidaților. Motorul financiar poate fi comun cu live, dar entrypoint-ul offline
primește un executor controlat și nu trebuie să aibă acces la capabilități private de
tranzacționare. Promovarea în producție cere testele și dovezile active descrise în
documentația de validare.

## Operare rapidă

Rulați comenzile din rădăcina repository-ului:

| Acțiune | Comandă |
|---|---|
| Stare read-only | `./healthcheck.sh --check` |
| Supraveghere procese `bot` | `./healthcheck.sh --supervise` |
| Pornire/restart flotă | `sudo systemctl restart binance` |
| Pornire boți independenți | `./bots_start.sh` |
| Verificare ownership | `.venv/bin/python verify_tools/ownership_inventory.py --running` |
| Snapshot portofoliu | `.venv/bin/python verify_tools/portfolio_snapshot.py` |
| Deploy controlat | `./deploy_providers.sh` |
| Backup secrete | `./backup_secrets.sh` / `./backup_remote.sh` |
| Refacere server | `./restore.sh <folder_secrete>` |

După orice schimbare în manifest sau configurare:

```bash
./healthcheck.sh --check
git status --short
```

Nu folosi `pkill -f` manual fără să verifici pattern-ul; scripturile de operare au
ordinea și excepțiile necesare pentru restart.

## Harta repository-ului

| Cale | Rol |
|---|---|
| `providers/` | contracte și adaptoare de market data/execution |
| `strategies/` | logică financiară reutilizabilă și state store |
| `binance_api/` | client și adaptor trailing Binance |
| `kraken/` | integrarea și procesele Kraken |
| `212trading/` | motorul și integrarea Trading 212 |
| `hyperliquid/` | integrarea Hyperliquid; DN oprit în producție |
| `forecast/` | estimări de trend și supraviețuire |
| `verify_tools/` | health, ownership, snapshot și validări operaționale |
| `offline/` | runners, simulări și replay izolate de live |
| `research/` | experimente și rezultate înainte de promovare |
| `tests/` | teste unitare, caracterizare și regresie |
| `docs/` | arhitectură, strategie, operații și disaster recovery |
| `systemd/` | unități pentru server și VPN |

## Documentație

- [`docs/README.md`](docs/README.md) — indexul documentației;
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — runbook, reboot și diagnostic;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — contracte, provideri și ownership;
- [`docs/STRATEGY.md`](docs/STRATEGY.md) — regulile financiare;
- [`docs/DISASTER_RECOVERY.md`](docs/DISASTER_RECOVERY.md) — backup și refacere;
- [`kraken/README.md`](kraken/README.md) și
  [`hyperliquid/README.md`](hyperliquid/README.md) — detalii per componentă.

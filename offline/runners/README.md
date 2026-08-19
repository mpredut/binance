# Runnere offline

Orchestrarea backtesturilor și a fluxului prod→dev. Scripturile se rulează din
rădăcina repo-ului sau prin căile absolute actualizate în `systemd/crontab.prod.txt`.

## Baseline generic prin adaptoare

Datasetul, ferestrele walk-forward și metricile sunt comune în
`offline/backtests/`. Engine-ul de decizie nu este comun: fiecare venue rulează
strategia sa live printr-un adaptor de replay.

```text
OHLC canonic + hash
        │
        ▼
evaluator walk-forward comun
        │
        ├── Kraken adapter ─────► kraken/Strategy.step
        └── Trading212 adapter ─► 212trading/Strategy.step
```

Kraken:

```bash
.venv/bin/python offline/runners/kraken_walk_forward_baseline.py
```

Trading212, folosind direct configurația versionată a profilului:

```bash
.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile nvda --range 2y --interval 1d

# SPCX are gate live pe bare Yahoo 5m; replay-ul refuză alte cadențe:
.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile spcx --range 1mo --interval 5m
```

Runnerul Trading212 nu accesează API-ul de ordine și nu poate plasa tranzacții.
Pentru profile non-USD descarcă și îngheață automat seria FX istorică; un CSV
propriu poate fi dat prin `--fx-dataset`. `--fx-to-usd` este doar override fix.

## Stres de execuție

Aceleași opțiuni există pe ambele runnere:

```bash
# HYPE: scenariu conservator, nu estimare calibrată a costurilor reale
.venv/bin/python offline/runners/kraken_walk_forward_baseline.py \
  --spread-bps 20 --market-slippage-bps 30 \
  --partial-fill-ratio 0.5 --intrabar-policy worst_case

# Trading212: ordinele actuale sunt limit; spread-ul afectează touch-ul,
# iar tranșa rămasă continuă în bara următoare
.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile nvda --range 2y --interval 1d \
  --spread-bps 10 --partial-fill-ratio 0.5 \
  --intrabar-policy worst_case
```

Comparatorul Kraken citește modelul din raportul baseline și îl aplică identic
tuturor candidaților și scenariilor de fee.

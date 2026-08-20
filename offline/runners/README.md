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

## Benchmark financiar HYPE și gate de promovare

Golden-ul verifică dacă motorul ia aceleași decizii după un refactor. Benchmark-ul
financiar măsoară separat randamentul și riscul OOS al unui profil fix în două
scenarii de execuție. Nu actualiza golden-ul pentru a „accepta” un candidat și nu
promova un candidat doar pentru că păstrează golden-ul.

```bash
.venv/bin/python offline/runners/kraken_financial_benchmark.py \
  --output offline/research/hype_dataset/financial_baseline_v1.json \
  --markdown chatgpt_agent_work/HYPE_FINANCIAL_BENCHMARK.md

# Reproducere exactă a baseline-ului versionat
.venv/bin/python offline/runners/kraken_financial_benchmark.py \
  --verify offline/research/hype_dataset/financial_baseline_v1.json \
  --output /tmp/hype_financial_verify.json \
  --markdown /tmp/hype_financial_verify.md
```

Pentru un candidat, `--params-report` citește un obiect `strategy_params` și
`--compare-to` atașează verdictul la raport. Gate-ul poate fi rulat și separat:

```bash
.venv/bin/python offline/runners/financial_promotion_gate.py \
  offline/research/hype_dataset/financial_baseline_v1.json \
  offline/results/hype_financial/candidate.json
```

Gate-ul cere avantaj de medie de minimum `0,10pp`, mai multe ferestre câștigate
decât pierdute și păstrarea worst-fold/DD în **ambele** scenarii. Valorile de cost
sunt provizorii până la calibrarea din fill-uri Kraken reale.

Setul HYPE prioritar (`tp4`, `dca15`, A, B și `overlay650t8`) se rulează batch,
fără grid search și fără modificarea configurației live:

```bash
.venv/bin/python offline/runners/kraken_financial_compare.py
```

Runnerul reproduce mai întâi baseline-ul live versionat și oprește comparația
dacă acesta diferă. Apoi aplică promotion gate fiecărui candidat în scenariile
central și stress.

Auditul real poate fi agregat read-only înainte de calibrarea costurilor:

```bash
.venv/bin/python offline/runners/calibrate_execution_audit.py \
  logger/execution_audit --venue Kraken \
  --output /tmp/kraken_execution_calibration.json \
  --markdown /tmp/kraken_execution_calibration.md
```

Raportul măsoară fee, latență, partial fills, abaterea fill-urilor LIMIT și, pentru
ordinele MARKET noi, shortfall-ul total dintre prețul deciziei și fill. Acest
shortfall include laolaltă mișcarea pieței, spread și slippage; raportul nu
pretinde că le poate separa fără bid/ask/mid salvat la decizie.

# Coada de validare pentru servere

Pregătită: 2026-08-20. Nu conține parole sau secrete.

## P0 — producție, numai citire înainte de orice deploy

1. Notează commitul, worktree-ul și procesele:

```bash
cd /home/predut/binance
git rev-parse HEAD
git status --short --branch
./healthcheck.sh --check
ps -eo pid,ppid,lstart,args | grep -E '[k]raken_bot.py|[t]212_bot.py'
```

2. Verifică starea Kraken, fără a modifica ordinele sau fișierele:

```bash
tail -n 120 kraken/kraken_bot.log
tail -n 120 logs/healthcheck.log
python3 verify_tools/ownership_inventory.py --running
```

3. Verifică shadow-ul existent și păstrează ancorele 60m/240m:

```bash
crontab -l
ls -lah logs/shadow_live/
tail -n 160 logs/shadow_live.log
tail -n 2 logs/shadow_live/HYPEUSD_60m.jsonl
tail -n 2 logs/shadow_live/HYPEUSD_240m.jsonl
```

Nu șterge și nu recrea fișierele `.anchor`, `.ohlc.json` sau `.jsonl`; altfel se
pierde forward-testul început anterior.

## P1 — calibrare din execuțiile reale Kraken

Rulează analizorul read-only pe auditul disponibil. Scrie rezultate numai în
`/tmp`, nu în starea live:

```bash
cd /home/predut/binance
TRADING_PYTHON=/home/predut/binance/myenv/bin/python
"$TRADING_PYTHON" offline/runners/calibrate_execution_audit.py \
  logger/execution_audit --venue Kraken \
  --output /tmp/kraken_execution_calibration.json \
  --markdown /tmp/kraken_execution_calibration.md
```

De colectat: număr ordine/fill-uri, fee bps limit/market, p50/p95 latență,
partial-fill rate și abaterea fill-urilor LIMIT. Auditul actual nu poate calibra
spread-ul sau slippage-ul MARKET deoarece nu conține bid/ask/mid la decizie.

## P1 — dev/backtest după sincronizarea codului

Pe mașina dev, după confirmarea commitului dorit:

```bash
cd /home/predut/binance
git fetch origin
git status --short --branch

.venv/bin/python offline/runners/kraken_financial_benchmark.py \
  --verify offline/research/hype_dataset/financial_baseline_v1.json \
  --output /tmp/hype_verify.json --markdown /tmp/hype_verify.md

.venv/bin/python offline/runners/kraken_financial_compare.py \
  --output /tmp/hype_candidates.json --markdown /tmp/hype_candidates.md

.venv/bin/python -m pytest -q
```

Compară hash-ul datasetului și valorile cu documentul
`chatgpt_agent_work/HYPE_FINANCIAL_CANDIDATES_V1.md`. Orice diferență trebuie
explicată înainte de shadow/deploy.

## P2 — activarea noului shadow, fără schimbare live

După ce branch-ul este mergeuit în `main` și codul este preluat pe producție:

- nu modifica `STRAT_DCA_SPACING_GROWTH_PCT=0` pentru botul live;
- nu reporni botul Kraken doar pentru shadow;
- lasă cronurile 60m/240m existente să încarce automat candidatul
  `dca_progressive025`;
- confirmă în următorul snapshot câmpurile `decision_trace` și
  `decision_divergences`;
- urmărește minimum 30 zile și minimum 20 divergențe de decizie față de `current`.

## P3 — condiții înaintea oricărei promovări

1. Gate central și stress trecut integral.
2. Minimum 20 divergențe forward, nu doar 20 snapshoturi repetate.
3. P&L net forward pozitiv față de live.
4. Max drawdown și cel mai slab regim nu sunt mai rele.
5. Rezultatul nu este dominat de o singură ieșire.
6. Costurile sunt rerulate cu fee-urile reale din audit.
7. Schimbarea live este separată, explicit aprobată și are plan de rollback.

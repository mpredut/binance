# HYPE — comparația financiară a candidaților v1

Data: 2026-08-20

## Verdict

Configurația live rămâne neschimbată. Niciun candidat nu trece gate-ul în
ambele scenarii. Rezultatele folosesc datasetul HYPE înghețat, 31 ferestre TEST
OOS de 90 bare 4h, stare resetată pe fereastră și același motor ca live.

| Candidat | Central mean | Δ central | Stress mean | Δ stress | Worst Δ central/stress | DD Δ central/stress | W/T/L central; stress |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tp4` | +0,601% | +0,011pp | +0,189% | -0,014pp | +1,616 / +1,602pp | -2,253 / -1,499pp | 3/25/3; 3/24/4 |
| `dca15` | +0,633% | +0,043pp | +0,297% | +0,093pp | +1,616 / +1,602pp | -1,726 / -1,499pp | 5/25/1; 5/26/0 |
| `dca_progressive025` | +0,586% | -0,003pp | +0,365% | +0,162pp | +1,616 / +1,602pp | -1,726 / -1,499pp | 6/23/2; 6/24/1 |
| `A_trail` | +0,409% | -0,181pp | +0,018% | -0,185pp | 0 / 0pp | 0 / 0pp | 11/12/8; 11/12/8 |
| `B_dcabrake` | +0,161% | -0,428pp | +0,014% | -0,189pp | +1,293 / +1,406pp | -2,159 / -1,499pp | 4/13/14; 5/13/13 |
| `overlay650t8` | +0,803% | +0,213pp | +0,280% | +0,077pp | +1,969 / +2,371pp | -3,005 / -2,973pp | 15/0/16; 14/0/17 |

Baseline: central `+0,590%`, stress `+0,203%`.

## Interpretare

- `tp4` nu mai este candidatul principal: tail-ul este mai bun, dar stress mean
  scade și robustețea pereche este insuficientă.
- `dca15` este cel mai bun one-factor existent: îmbunătățește consistent tail/DD
  și pierde o singură fereastră central, zero stress. Efectul mediu rămâne însă
  sub pragul preînregistrat de `+0,10pp`.
- `dca_progressive025` este un mecanism nou, implicit oprit: primul DCA rămâne la
  `1,25%`, apoi pragul crește cu `0,25pp` după fiecare DCA executat. Este neutru
  central și mai bun sub stress; avansează numai în shadow read-only.
- `B` este prea brutal: protejează tail-ul, dar sacrifică randamentul.
- `overlay650t8` are cea mai mare medie centrală și cel mai bun tail, însă pierde
  mai multe ferestre decât câștigă și nu atinge îmbunătățirea minimă în stress.
- `A` rămâne respins.

## Reproducere

```bash
.venv/bin/python offline/runners/kraken_financial_benchmark.py \
  --verify offline/research/hype_dataset/financial_baseline_v1.json \
  --output /tmp/hype_verify.json --markdown /tmp/hype_verify.md

.venv/bin/python offline/runners/kraken_financial_compare.py \
  --output /tmp/hype_candidates.json --markdown /tmp/hype_candidates.md
```

Runnerul nu schimbă configurația live. Promotion gate cere simultan în central și
stress: minimum `+0,10pp` medie, tail și DD păstrate, minimum 20 ferestre și mai
multe ferestre câștigate decât pierdute.

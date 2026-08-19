# HYPE dataset înghețat (proxy Hyperliquid)

Dataset reproductibil pentru re-rularea INSTANT a oricărui candidat de strategie Kraken/HYPE,
fără dependență de fetch live (Kraken public OHLC dă doar ~120 zile; aici avem ~628).

- `HYPEUSDC_240m_hlspot.csv` — 3772 bare de 4h (~628 zile)
- `HYPEUSDC_1440m_hlspot.csv` — 628 bare de 1 zi
- `manifest.json` — sursă, hash sha256, dată fetch

**Sursă:** Hyperliquid public OHLC (`api.hyperliquid.xyz`), HYPE/USDC spot, via
`offline/runners/fetch_hyperliquid_candles.py`. **PROXY cross-venue** pentru mișcarea prețului
HYPE — NU execuția Kraken (fill-urile reale diferă). Bun pentru robustețe, nu pentru absolut.

## Verificare candidați reproductibilă

Schema declarată pentru comparația HYPE este fixă: 720 bare TRAIN, 180 VALIDATION,
90 TEST, pas 90, warm-up de semnal 40 bare și fee 0,26%/leg. Pentru cele 3.772
bare de 4h rezultă exact 31 ferestre TEST. Warm-up-ul încarcă numai SMA/vol/trend;
poziția, ordinele și P&L-ul pornesc curate în fiecare segment.

```bash
.venv/bin/python offline/runners/kraken_walk_forward_baseline.py \
  --intervals 240 \
  --dataset 240=offline/research/hype_dataset/HYPEUSDC_240m_hlspot.csv \
  --train 720 --validation 180 --test 90 --step 90 --warmup 40 \
  --output-dir offline/results/hype_long_31

.venv/bin/python offline/runners/kraken_walk_forward_compare.py \
  offline/results/hype_long_31/baseline_HYPEUSD_<timestamp>.json \
  --candidate-set hype-240
```

Testul de contract verifică fișierele față de manifest, baseline-ul înregistrează
hash-ul, iar comparatorul îl reverifică înainte de evaluarea candidaților. Cifrele
de mai jos au fost regenerate după fixurile de fill/warm-up, pe schema declarată
mai sus. Raportul regenerat rămâne sursa de adevăr dacă engine-ul se schimbă.

## Rezultat reproductibil (31 ferestre OOS, warm-up 40, fee 0,26%/leg)

BASE v2: medie **+0,777%**, cea mai slabă fereastră `−9,123%`, DD max `12,305%`.

| Candidat | Δ medie vs base | W/T/L | worst fold | DD max | verdict |
|---|---|---|---|---|---|
| **overlay orig** (topup 2000/trail 5) | +0,530pp | 15/2/14 | **−12,612%** | **19,212%** | RESPINS — media vine cu tail/DD mai rele |
| **overlay650t8** | +0,637pp | 15/0/16 | **−7,373%** | **9,105%** | candidat shadow; selectat pe proxy, pierde totuși 16/31 perechi |
| **A** trailing-adaptiv | −0,368pp | 11/12/8 | −9,460% | 12,305% | nu promovează: medie mai slabă, fără avantaj de tail |
| **B** frână-DCA-downtrend | −0,457pp | 5/14/12 | **−7,829%** | 10,113% | reduce tail/DD, dar sacrifică randamentul |
| **tp4** (TP 5→4) | +0,054pp | 3/25/3 | −7,398% | 10,113% | marginal — aproape mereu identic |
| **dca15** (DCA 1,25→1,5) | +0,005pp | 7/21/3 | −8,858% | 12,213% | practic inert |

Aceste ferestre se suprapun prin train/validation și provin din același price-path;
nu sunt 31 experimente statistic independente. Concluzia solidă este de screening:
A nu justifică promovarea, overlay-ul original amplifică riscul, iar B schimbă
randament pe protecție. `overlay650t8` necesită confirmare Kraken/shadow deoarece a
fost ales după explorarea aceluiași proxy.

## Concluzie (aliniată cu revalidarea Codex 19 aug)
- **Overlay original: respins** — motivul exact este **instabilitatea + riscul de
  coadă**, nu pierdere uniformă la medie (câștigă 15/31 comparații, dar tail-ul
  este mult mai slab decât la base).
- **A / tp4 / dca15:** fără avantaj suficient → doar shadow, fără promovare.
- **B:** OFF pe randament, dar unghiul de **protecție tail/DD** e real și subexplorat.
- **Live neschimbat.** Prag de promovare: min 30 zile + 20 evenimente de divergență în shadow.

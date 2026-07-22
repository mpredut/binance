# Investigație: declanșatoare BUY/SELL în tradeall.py (21-22 iul 2026)

Scripturi izolate folosite pentru a testa dacă merită mărită frecvența cu care
`tradeall.py` declanșează ordine reale. **Niciunul dintre aceste scripturi nu
modifică `tradeall.py` pe disc** — toate importă `tradeall`/`tradeall_backtest`
și suprascriu (monkeypatch) funcții/clase în memorie, doar pentru durata
rulării lor proprii. Sunt scripturi de cercetare, nu cod de producție — nu
rulează niciodată împotriva rețelei reale (folosesc `tradeall_backtest.py`,
care simulează execuția).

Concluzia completă (cifre, tabele, recomandare finală) e în memoria
persistentă a asistentului: `tradeall-trigger-gate-investigation.md`
(căutabilă/reference-abilă din orice sesiune viitoare Claude Code pe acest
repo). Rezumat pe scurt: **nu s-a găsit nicio schimbare de praguri sau
condiții care să bată varianta actuală + buy&hold, testat pe eșantioane de la
12 ore până la 329 de zile.** Singurul lucru care a arătat o îmbunătățire
reală (fără să schimbe deloc detecția de trend) e cooldown-ul — vezi
Experimentul 7.

## Scripturi, în ordine cronologică

- **`experiment_trend_gate.py`** (Experiment 1) — variază pragurile de
  CONFIRMARE/EXPIRARE ale unui trend deja pornit în `TrendState`
  (`expiration_trend_time`, `TREND_TO_BE_OLD_SECONDS`, pragul de 24 confirmări).
  Concluzie: relaxarea lor nu creează oportunități noi, doar prelungește
  refirerea aceluiași eveniment.

- **`experiment_start_condition.py`** (Experiment 2) — variază condiția de
  START a unui trend (`gradient>0 and slope_big<0` în `logic()`): eliminare
  completă (doar semnul gradientului), cerință de acord în loc de divergență,
  prag `PRICE_CHANGE_THRESHOLD_BIG_EUR` redus de 10x. Concluzie: orice
  relaxare care chiar crește frecvența produce overtrading catastrofal
  (zeci de mii de $ în comisioane pe doar 2-7 zile).

- **`experiment_cooldown.py`** (Experiment 3) — adaugă un cooldown fire-once
  (cel mult un ordin per instanță de trend, nu refire la fiecare tick) peste
  variantele din Experimentul 2. Reduce dramatic overtrading-ul, dar nu
  transformă singur strategia într-una profitabilă dacă semnalul de bază
  rămâne zgomotos.

- **`experiment_dual_timeframe.py`** (Experiment 4) — testează ideea de
  "acord" pe două ferestre de timp folosind aceeași derivare continuă
  (regresie) pe fereastra mică ȘI pe cea mare (`gradient_big` în loc de
  `slope_big`). Concluzie: cele două ferestre nu sunt semnale independente —
  ambele sunt regresii pe fereastră glisantă, deci zgomotoase în același fel.

- **`experiment_quality_signal.py`** (Experiment 5) — un semnal de trend
  CALITATIV diferit: regresie pe fereastră de 24h, recalculată doar la 30 min
  (nu la fiecare tick) + cooldown pe execuție CONFIRMATĂ (nu pe simpla
  încercare). Testat pe 7 zile — a arătat un rezultat aproape de buy&hold,
  dar a expus un defect: reîncercări nelimitate la fiecare tick când nu exista
  poziție de vândut (16683 încercări blocate pe BTC într-o săptămână).

- **`experiment_quality_signal_v2.py`** (Experiment 6) — repară defectul de
  mai sus (interval minim de 30 min între reîncercări blocate) și testează pe
  **329 de zile** de istoric real (`cache_price_*.jsonl`, sparse ~7 min/tick).
  Rezultat decisiv: toate cele 4 configurații (2 ferestre × 2 simboluri) au
  pierdut bani și au rămas sub buy&hold — rezultatul optimist de pe 7 zile
  s-a dovedit noroc de eșantion mic.

- **`experiment_cooldown_only_and_tighten.py`** (Experiment 7) — testează
  DOAR cooldown-ul (execuție confirmată + interval minim), fără nicio altă
  schimbare de prag, aplicat pe `logic()` REALĂ din `tradeall.py` (condiția de
  start neschimbată) — răspunde direct la întrebarea "merită comis doar
  cooldown-ul?". Testează și o variantă ÎNĂSPRITĂ (prag de confirmări dublat,
  24→48). **Notă metodologică importantă**: folosește arhiva DENSĂ (~1s/tick,
  7 zile), nu istoricul sparse de 329 de zile — mecanismul original din
  `logic()` are constante de timp scurte (expirare 2.7 min), incompatibile cu
  eșantionarea de 7 min a istoricului lung (orice trend ar "expira" instant,
  ca artefact al rarității datelor, nu al pieței reale).

## Cum rulezi orice script din acest folder

Toate scripturile presupun `cwd = /home/predut/binance` și folosesc `myenv`:

```bash
cd /home/predut/binance
source myenv/bin/activate
python3 research/tradeall_trigger_gate/<script>.py
```

Scriu rezultate în `logger/backtest/experiment{N}_*/` (pnl.json, order_outcomes.log
etc.) — același format ca backtest-urile normale, vizualizabile cu
`tradeall_observe.py --backtest-dir ...`.

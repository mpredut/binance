# Restanțe active — producție și prioritate financiară

Data consolidării: 2026-08-20

## Starea de la care continuăm

- Codul provider-agnostic și fundația de validare sunt integrate în `main`.
- Suita locală pe codul final: `762 passed`, `235 subtests passed`.
- Benchmark HYPE reproductibil: `VERIFY OK`.
- Baseline HYPE: central `+0,590%`, stress `+0,203%`.
- Niciun candidat existent nu este aprobat pentru bani reali.
- Configurația live rămâne sursa de comparație; shadow nu schimbă ordinele live.

## A. De rulat pe producție când revine accesul

### P0 — pre-deploy, read-only

1. Validează fingerprint-ul SSH cunoscut.
2. Notează commitul, statusul worktree-ului și configurația efectivă.
3. Rulează healthcheck-ul și inventariază procesele cu PID/PPID/start time/argumente.
4. Verifică ultimele loguri Kraken/T212 și absența erorilor noi, restart loop-urilor
   sau proceselor duplicate.
5. Verifică pozițiile, ordinele deschise și continuitatea stării înainte de orice
   restart. Fluctuația normală de P&L nu este incident operațional.

### P1 — deploy controlat, numai cu aprobare explicită

1. Preia `main` numai dacă worktree-ul de producție este curat și commitul remote
   este cel aprobat.
2. Nu modifica parametrii live ai candidaților: spacing growth și volatility sizing
   rămân implicit `OFF`.
3. Nu șterge/recrea ancorele sau fișierele shadow 60m/240m.
4. Repornește numai procesele care trebuie să încarce cod live nou și numai după
   reconcilierea pozițiilor/ordinelor; confirmă exact numărul așteptat de procese.
5. Monitorizează după restart heartbeat, reconciliere, ordine duplicate și erori
   până la minimum un ciclu operațional relevant.

### P2 — shadow și calibrarea execuției

1. Confirmă că snapshoturile 60m/240m conțin `decision_trace` și
   `decision_divergences` și că ancorele vechi au fost păstrate.
2. Acumulează minimum 20 divergențe reale de decizie înainte de verdict forward;
   snapshoturile identice nu contează drept dovezi independente.
3. Confirmă în audit că ordinele MARKET au `reference_price` la intenție, dar
   ajung la provider cu `price=null`.
4. După minimum 20 ordine cu fill, rulează calibrarea read-only pentru fee,
   latență, partial-fill rate, abaterea LIMIT și decision-to-fill shortfall.
5. Actualizează scenariile central/stress numai din distribuțiile observate, apoi
   regenerează baseline-ul și toți candidații pe aceleași costuri.

## B. Priorități financiare locale

### F1 — două culoare de promovare

Extinde gate-ul actual fără să schimbi strategia live:

```text
Candidat -> gate-uri comune -> RETURN gate ------> eligibil RETURN
                           \-> DEFENSIVE gate ---> eligibil DEFENSIVE
```

- `RETURN`: minimum `+0,10pp` medie în central și stress, tail/DD păstrate,
  minimum 10 ferestre active, mai multe wins decât losses și sign-test `p<=0,10`.
- `DEFENSIVE`: return non-inferior în ambele scenarii, return pozitiv, Calmar
  median material mai bun, DD/CVaR material mai mici și fără buget/expunere mai mare.
- Verdictul `OR` produce o etichetă (`RETURN` sau `DEFENSIVE`), nu o promovare live
  nediferențiată. Urmează obligatoriu shadow și aprobare explicită.

Ordinea reevaluării:

1. `dca15` — cel mai bun candidat one-factor pentru pista defensivă;
2. `dca_progressive025` — neutru central, mai bun în stress;
3. `dca_vol_m1` — control final, probabil respins deoarece median Calmar și Sortino
   sunt mai slabe chiar dacă worst-fold/DD se îmbunătățesc.

### F2 — baseline Trading212

1. Îngheață dataseturile Yahoo per profil NVDA/RGNT/SPCX cu manifest și hash.
2. Rulează configurația live exactă prin același engine live/replay.
3. Generează central/stress, walk-forward temporal și benchmark buy-and-hold.
4. Profilele curente sunt USD, deci FX istoric nu este necesar acum; se activează
   numai pentru un profil cu moneda contului diferită de moneda activului.
5. Preînregistrează maximum câțiva candidați one-factor înaintea comparației.

### F3 — propunerile Binance, după T212

Revalidează pe codul și datele curente, fără promovare directă, ipotezele vechi:

- `mt.gain`: `5,5 -> 5,0`;
- `mt.maxage_days`: `10,5 -> 14`.

Rezultatele existente provin dintr-un commit mai vechi și din numai două jumătăți
de eșantion; au nevoie de walk-forward, costuri și gate-urile curente.

## Ce nu mai merită prioritate acum

- overlay-ul HYPE și candidatul `A_trail`: respinși OOS;
- branch-ul vechi `kraken-trail-decay-v3`: metrici greșite și arhitectură depășită;
- tuning suplimentar pentru `dca_vol_m1` înaintea gate-ului defensiv;
- rutarea STOP/trailing prin `MarketApi.place` (Faza 6);
- refactorizări mari de cache sau entrypoint fără un defect/operație concretă.

## Definiția următoarei promovări

Un candidat poate ajunge live numai dacă trece gate-ul central și stress potrivit
rolului său, are suficiente ferestre/divergențe active, rămâne bun după costurile
calibrate, nu este dominat de un singur regim/trade, trece shadow forward și are
deploy plus rollback aprobate separat.

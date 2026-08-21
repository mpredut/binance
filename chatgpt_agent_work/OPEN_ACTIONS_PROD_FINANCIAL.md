# Restanțe active — producție și prioritate financiară

Data consolidării: 2026-08-21

## Starea de la care continuăm

- Codul provider-agnostic și fundația de validare sunt integrate în `main`.
- Suita locală pe codul final: `773 passed`, `235 subtests passed`.
- Benchmark HYPE reproductibil: `VERIFY OK`.
- Baseline HYPE: central `+0,590%`, stress `+0,203%`.
- Niciun candidat existent nu este aprobat pentru bani reali.
- Configurația live rămâne sursa de comparație; shadow nu schimbă ordinele live.

## A. Validare și deploy producție

### P0 — pre-deploy, read-only — FINALIZAT 2026-08-20

- Fingerprintul ED25519 cunoscut a fost validat înainte de autentificare.
- Worktree-ul era curat; healthcheck-ul și inventarul PID/stare/ordine au fost
  verificate înainte de restart.
- Nu existau traceback-uri, procese duplicate, restart loop sau stare coruptă.

### P1 — deploy controlat — FINALIZAT 2026-08-20

- Producția rulează checkout curat `main@b65554e`.
- Au fost repornite controlat numai HYPE și Trading212. ADA paper și TAO live nu
  au fost atinse.
- HYPE a încărcat ciclul 14 cu `qty=0`, `orders=0`; Trading212 a încărcat aceleași
  trei profiluri, cantități și `0/1/2` ordine urmărite. Nu s-au creat duplicate.
- Ambele procese sunt unice și au `PPID 1`; healthcheck-ul final este integral OK.
- Parametrii live și ancorele shadow nu au fost modificați.

### P2 — shadow și calibrarea execuției — ÎN AȘTEPTARE DE DATE

- Ancorele originale 60m/240m sunt păstrate. Snapshoturile sunt proaspete și au
  `decision_trace` plus `decision_divergences`.
- Forward-ul are 43 bare la 60m și 12 bare la 240m, dar încă zero divergențe pentru
  toți candidații; nu există încă dovadă forward activă.
- Calibrarea read-only vede 27 ordine: 26 LIMIT și 1 MARKET. Există 3 fill-uri
  din minimum 20 necesare; fee p50 este 30 bps, iar singurul MARKET are shortfall
  3,149 bps. Eșantionul este încă insuficient, deci costurile central/stress rămân
  necalibrate.
- Următorul pas se execută numai după acumularea datelor, nu prin tuning acum.

Observații operaționale separate de deploy:

- Healthcheck-ul din 2026-08-21 este integral OK pentru cele 14 procese din manifest.
- Inventarul runtime raportează `WARNING` pe `kraken:default/HYPEUSD`: motorul spot,
  trailing-ul protector și `monitortrades` revendică același simbol. Nu este un defect
  demonstrat, dar ownership-ul trebuie reverificat înaintea unei schimbări de execuție.

- T212 a respins un DCA SPCX pentru fonduri insuficiente și a păstrat corect
  backoff-ul de 30 minute peste restart; nu s-a creat ordin.
- Canalul `ntfy` a răspuns `429` (cotă zilnică epuizată), deci notificările prin
  acel canal sunt temporar degradate; bucla de tranzacționare a continuat normal.

## B. Priorități financiare locale

### F1 — două culoare de promovare — FINALIZAT

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

Rezultatul reevaluării:

1. `dca15` — Calmar median neschimbat și numai 5–6 fold-uri DD active;
2. `dca_progressive025` — 7–8 fold-uri DD active, tot sub prag;
3. `dca_vol_m1` — respins: Calmar median `-21,9%/-23,2%` central/stress.

Niciun candidat nu trece `RETURN` sau `DEFENSIVE`; live rămâne neschimbat.

### F2 — baseline Trading212 — FINALIZAT CA FUNDAȚIE

1. Îngheață dataseturile Yahoo per profil NVDA/RGNT/SPCX cu manifest și hash.
2. Rulează configurația live exactă prin același engine live/replay.
3. Generează central/stress, walk-forward temporal și benchmark buy-and-hold.
4. Profilele curente sunt USD, deci FX istoric nu este necesar acum; se activează
   numai pentru un profil cu moneda contului diferită de moneda activului.
5. Preînregistrează maximum câțiva candidați one-factor înaintea comparației.

### F3 — propunerile Binance — REVALIDAT, FĂRĂ SCHIMBARE

Ipotezele vechi au fost revalidate pe aproximativ 894.000 observații BTC:

- `mt.gain`: prima jumătate preferă `7,0`, a doua `5,0`;
- `mt.maxage_days`: prima jumătate preferă `14`, a doua valoarea live `10,5`.

Propunerile nu mai sunt confirmate în ambele ferestre; configurația rămâne
`5,5` și `10,5`.

## Ce nu mai merită prioritate acum

- overlay-ul HYPE și candidatul `A_trail`: respinși OOS;
- `trail-decay v3`, inclusiv portarea nouă de pe `feature/calmar-gate`: aproximativ
  `+0,05pp` return, numai `+8%` Calmar și zero reducere DD; pică ambele gate-uri,
  deci rămâne pe branch și nu intră în `main`;
- tuning suplimentar pentru `dca_vol_m1` înaintea gate-ului defensiv;
- rutarea STOP/trailing prin `MarketApi.place` (Faza 6);
- refactorizări mari de cache sau entrypoint fără un defect/operație concretă.

## Definiția următoarei promovări

Un candidat poate ajunge live numai dacă trece gate-ul central și stress potrivit
rolului său, are suficiente ferestre/divergențe active, rămâne bun după costurile
calibrate, nu este dominat de un singur regim/trade, trece shadow forward și are
deploy plus rollback aprobate separat.

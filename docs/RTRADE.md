# rtrade — politică financiară, execuție și operare

Document de referință pentru `rtrade.py`, `strategies/rtrade_pair.py` și
`rtrade_pair_store.py`. Configurația efectivă rămâne `rtrade_config.env`; valorile de
mai jos descriu profilul live din 23 august 2026.

## Verdict financiar

rtrade este un market-making/spread bot spot pe `TAOUSDC`. Mecanica urmărește să
încaseze diferența dintre un BUY sub prețul median și un SELL peste acesta, reducând
riscul operațional al ordinelor nepereche. **Nu există însă dovadă că strategia are
randament pozitiv sau că maximizează profitul.**

Backtestul exploratoriu pe cache-ul TAO a fost negativ pentru toate combinațiile
testate de spread și stop. Datele aveau pas median de aproximativ 19 secunde și nu
puteau valida clasificarea fast-fill de 8 secunde. Activarea live trebuie deci tratată
ca observație forward cu capital real și limite de risc, nu ca edge demonstrat.

Mecanismele de mai jos au două roluri distincte:

- profit-seeking: spread, profit guard și exit ancorat în fill;
- loss/risk control: ownership per rundă, reconcilierea fillurilor, plafonul de
  concurență, backoff și ieșirea de urgență.

Un control de risc poate reduce pierderile accidentale, dar nu transformă singur o
strategie fără edge într-una profitabilă.

## Intrarea și ținta de profit

- Coordinatorul este activ, cu maximum 4 runde simultane.
- O rundă nouă poate porni la minimum 8 secunde.
- Direcțiile alternează `BUY-first` și `SELL-first`.
- Notionalul cerut este 500 USDC/rundă; cantitatea finală poate fi mai mică.
- Ajustarea curentă este 0,64% pe fiecare parte a midului. Teoretic, înainte de
  rotunjire, comisioane și slippage, distanța BUY–SELL este aproximativ 1,28% din mid.
- Marja minimă a exitului este 1,15%, iar fee-cap-ul Binance folosește estimarea de
  0,1% pe ordin. Aceste praguri protejează prețul cerut, dar fill probability,
  selecția adversă și slippage-ul pot elimina câștigul teoretic.

SELL-first pe Binance Spot nu deschide short cu împrumut. Vinde numai TAO disponibil
în cont și apoi încearcă buyback. În ledgerul rundei apare expunere `SOLD`, dar la
nivelul contului aceasta înseamnă că inventarul TAO a fost redus. Riscul financiar
este recumpărarea mai scumpă într-o piață ascendentă.

## Ciclul unei runde

1. Calculează un BUY și un SELL în jurul midului.
2. Plasează ambele ordine prin `mkt.place` → `Instrument.place` → provider Binance.
3. Atașează un `pair_id` și client-order-ID determinist `RT_...` fiecărui picior.
4. Dacă al doilea picior nu poate fi plasat, primul este anulat.
5. Fără fill până la TTL (32 secunde), ambele sunt anulate și starea este recitită
   pentru a închide cursa fill-versus-cancel.
6. Cu un singur fill sau partial fill, remainder-ul de entry este anulat, expunerea
   netă este recalculată din fillurile exchange-ului, iar exitul opus este
   redimensionat la acea cantitate.
7. Alte runde pot porni între timp; fiecare își deține separat ordinele și ledgerul.
8. Runda devine terminală numai la pereche completă, expirare fără fill, eșec
   controlat sau hard-stop executat.

Rundele nu rezervă sold între ele. Pipeline-ul recalculează soldul înainte de fiecare
submit, deci concurența produce clamp/refuz controlat, dar poate reduce probabilitatea
ca ambele picioare ale aceleiași runde să fie acceptate.

## Guard-uri și cantitate

Ordinele limit normale trec prin pipeline-ul comun:

```text
requested_qty
  -> balance_cap
  -> daily/weight policy_cap
  -> fee_cap
  -> precizie și minimum Binance
  -> final_qty sau refuse_reason
```

Se aplică profit guard, plafon zilnic, trend-wait, cooldown per `pair_id`, sold liber,
fee-cap și validările Binance. La sold zero sau alt refuz, direcția intră în backoff
180 secunde. `caller_owns_retry=True` exclude ordinele rtrade din outbox-ul global;
coordonatorul este singurul owner al retry/reconcile.

Aceste garduri sunt conservative. Ele pot evita tranzacții dezavantajoase, dar pot și
reduce mult numărul de filluri. De exemplu, atingerea plafonului zilnic poate lăsa
rtrade sănătos, dar fără ordine active.

## Expunere, trend și stop

După un fill unilateral, exitul limită este ancorat în prețul mediu real al intrării
și în edge-ul minim; nu urmărește automat piața în pierdere.

Pragurile curente sunt:

- fast-fill/shock: fill în maximum 25% din TTL, adică aproximativ 8 secunde;
- prag de evaluare hard-stop shock: 4%;
- prag de evaluare hard-stop normal: 8%;
- prag de urgență: 12%.

În `RTRADE_DYNAMIC_MARKET_EXIT_MODE=live`, 4% și 8% **nu garantează** un ordin MARKET.
La aceste praguri, MARKET este permis numai dacă `MarketRegimeDecision` confirmă trend
advers expunerii. Dacă nu confirmă, runda păstrează exitul ancorat și așteaptă. La 12%,
urgența permite MARKET indiferent de semnal. Aceasta reduce vânzarea panicată într-o
mișcare temporară, dar asumă explicit tail-risk între pragul inițial și 12% dacă
detectorul de trend greșește sau întârzie.

MARKET este permis doar pentru reducerea unei expuneri deja create. Cantitatea este
reconciliată din nou cu soldul, fee-cap-ul, precizia și minimul venue-ului.

## Persistență și recovery

`cachedb/rtrade_pairs.json` păstrează intenția canonică înainte de submit, valorile
cerute și acceptate, order ID-ul și checkpointul coordonatorului. Ordinele LIMIT trec
prin `order_retry.TrackedOrderLifecycle`, dar rămân în state-ul rtrade și nu în
outbox-ul global. Fiecare apel lifecycle face un singur submit și nu așteaptă
terminalul. Dacă răspunsul nu conține order ID, rtrade face un lookup imediat; numai
absența confirmată permite un al doilea apel cu același client ID. Nu există loop de
submit sau așteptare activă. La restart:

- intenție + ordin existent: ordinul este adoptat;
- intenție fără ordin după absență confirmată: un submit idempotent cu același client ID;
- răspuns de submit pierdut: lookup după client ID, fără submit concurent;
- ordin `RT_` fără ownership local: anulare și confirmare automată;
- stare ambiguă/API indisponibil: fail-closed, fără ordin speculativ.

Sunt păstrate maximum 200 de runde terminale. Checkpointurile active sunt scrise în
batch, ordinele terminale cu fill zero sunt compactate, iar cache-urile auxiliare au
plafoane de memorie.

Lifecycle-ul comun rezolvă mecanica de persistență, lookup și status. `PairCoordinator`
rămâne ownerul politicii de TTL, cancel/reprice, partial fill și hard-stop. Calea
legacy `repetitive_buy`/`repetitive_sell` rămâne disponibilă în spatele feature
flagului și nu este consumată de workerul global.

## Invariante operaționale

- maximum 4 coordonatoare active și minimum 8 secunde între runde;
- exact un owner (`pair_id`) pentru fiecare picior rtrade;
- nicio ieșire MARKET în afara unei expuneri existente și a politicii de stop;
- P&L și `net_qty` calculate din filluri, nu din cantitatea cerută;
- aceeași intenție produce același client-order-ID după restart;
- rtrade nu folosește simultan retry-ul local și outbox-ul global;
- lipsa certitudinii despre exchange blochează submitul nou, nu inventează stare.

## Ce trebuie urmărit pentru validare financiară

- P&L net după comisioane și slippage, nu cashflow brut;
- rata rundelor complet pereche și timpul mediu de expunere unilaterală;
- pierderea condiționată de BUY-first versus SELL-first;
- fill probability la spreadul de 0,64%;
- câte intrări sunt refuzate de profit guard/plafon zilnic;
- drawdown și pierderea la percentilele extreme;
- deciziile de trend la pragurile 4%/8% și execuțiile de urgență la 12%;
- rezultate forward suficiente înainte de mărirea notionalului sau concurenței.

Testele deterministe validează mecanica și recovery-ul, nu profitabilitatea. Orice
schimbare a spreadului, stopurilor, notionalului sau numărului de runde cere replay,
testele complete și observație forward.

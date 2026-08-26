# Order retry, lifecycle și starea strategiilor

Acesta este designul canonic pentru ordine persistente. `order_retry` centralizează
mecanica ordinului; strategia rămâne autoritatea pentru semnal, buget, campanie și
decizia financiară de după un status terminal.

## Cele două fluxuri

### 1. Outbox-ul global

```text
strategie fără lifecycle propriu
  -> Instrument.place
  -> persistă intenția în cachedb/order_retry_queue.jsonl
  -> face un singur submit, fără polling terminal
  -> order_retry_worker.py citește ulterior outbox-ul
  -> lookup/status/retry conform stării recordului
```

`order_retry.py` este o bibliotecă. Definește formatul outbox-ului, operațiile
atomice și state machine-ul comun; nu rulează singur. `order_retry_worker.py` este
procesul OS unic care consumă outbox-ul global. Numai bucla sa principală apelează
`process_once`; un apel `Instrument.place` nu pornește workerul și nu așteaptă ca
ordinul să devină terminal.

`RETRY_DEDUP=false` păstrează record separat pentru fiecare intenție. Două intenții
independente pe același simbol și aceeași direcție nu se suprascriu. Idempotency-ul
unei singure intenții este dat de `intent_id` și `client_order_id`, nu de deduplicare
după `symbol + side`.

### 2. Lifecycle deținut de strategie

```text
strategie stateful
  -> TrackedOrderLifecycle.submit
       -> callback atomic în JSON-ul strategiei
       -> un singur submit
  -> următorul tick/restart
       -> TrackedOrderLifecycle.reconcile
       -> lookup client ID, status și terminal
  -> strategia aplică fill-ul și actualizează campania
```

Acest flux folosește `caller_owns_retry=True`, deci ordinul nu intră și în outbox-ul
global. Nu există două monitoare concurente. `TrackedOrderLifecycle` este o clasă
apelată în tickurile procesului strategiei, nu un thread și nu un daemon ascuns.

## Ce sunt threadurile de cache din procesul worker

Un proces `order_retry_worker.py` poate avea mai multe threaduri după ce atinge calea
Binance. Acestea nu sunt consumatori suplimentari ai cozii:

- `MainThread` deține bucla unică `process_once` și citește JSONL-ul;
- `BinanceTimeResync` menține offsetul ceasului pentru requesturile semnate;
- managerii creați lazy de `cacheManager.CacheFactory` actualizează cache-uri precum
  ordine, filluri/trades sau preț curent, atunci când profit guard-ul și pipeline-ul
  Binance le cer;
- threadurile de trend/cache (`InstantTrend...`, price managers sau websocket) apar
  numai dacă acea infrastructură este inițializată în proces.

Fiecare `CacheManagerInterface.periodic_sync` are propriul thread daemon. El
alimentează datele folosite de guarduri; nu citește `order_retry_queue.jsonl`, nu
retransmite ordine și nu schimbă ownership-ul lifecycle. Numărul exact de threaduri
depinde de cache-urile cerute lazy în acel proces. Separarea unui provider Binance
minimal pentru worker ar putea reduce aceste threaduri, dar trebuie caracterizată
înainte, deoarece gardurile curente depind de istoricul din cache.

## Contractul comun al unei intenții

Câmpurile mecanice importante sunt:

```text
intent_id, client_order_id, venue, symbol, side, kind
requested_qty, requested_price, attempt, created_at
order_id, submitted_qty, submitted_price, submit_status
lookup_misses, last_status, filled_qty, terminal_status
```

Ordinea obligatorie este: persistare durabilă, apoi efect extern. Un răspuns de submit
pierdut lasă intenția persistentă fără `order_id`; reconcilierea caută mai întâi după
`client_order_id`. O eroare de lookup este ambiguă și blochează retransmiterea. Numai
absența confirmată conform politicii ownerului poate elibera intenția pentru retry.

## rtrade după refactor

Calea activă `PairCoordinator` folosește acum lifecycle-ul comun pentru ordinele
LIMIT și pentru recovery-ul de startup:

```text
PairCoordinator
  -> _LivePairVenue.place_limit
  -> TrackedOrderLifecycle.submit
  -> RTradePairStore.persist_intent
  -> mkt.place(... caller_owns_retry=True)
```

Submitul nu face polling și nu blochează până la fill. Dacă primul răspuns nu conține
`order_id`, calea live face o singură reconciliere imediată, fără sleep-loop. Ordinul
găsit este adoptat; numai după absență confirmată este permis un al doilea submit
idempotent cu același client ID. Lookup/status indisponibil păstrează intenția și
blochează rundele noi. La restart, `recover_intent` transformă și recordurile vechi în
formatul canonic, caută ordinul după client ID și citește statusul normalizat.

`PairCoordinator` păstrează intenționat politica per-tick pentru TTL, cancel,
repricing, partial fill și hard-stop. Acestea sunt decizii financiare ale rundei, nu
mecanică generică. `repetitive_buy` și `repetitive_sell` rămân calea legacy din
spatele feature flagului și nu au fost eliminate.

## Unde se salvează starea

Fișierele sunt relative la rădăcina repository-ului, dacă nu este specificată o
cale prin configurare:

| Owner | Fișier/pattern |
|---|---|
| outbox global | `cachedb/order_retry_queue.jsonl` + `.lock` |
| rtrade | `cachedb/rtrade_pairs.json` + `.lock` |
| AssetGuardian | `cachedb/assetguardian_state.json` |
| cooldown comun Binance | `lock/trade_cooldown.json` + `.lock` |
| trailing Binance | `cachedb/trailing_state.json` |
| trailing Kraken | `kraken/trailing_state.json` |
| Kraken spot-DCA | `kraken/.state_<PAIR>.json` |
| Hyperliquid spot-DCA live | `hyperliquid/.state_<TOKEN>.json` |
| Hyperliquid spot-DCA paper | `hyperliquid/.paper_state/.state_<TOKEN>.json` |
| Hyperliquid direcțional legacy | `hyperliquid/.state_<COIN>_<direction>.json` |
| Hyperliquid delta-neutral | `hyperliquid/.state_dn_<COIN>.json` |
| Trading212 per ticker | `212trading/.state_<TICKER>.json` |

Pentru rtrade, JSON-ul are `pairs[pair_id]`. Fiecare rundă conține identitatea,
cantitatea, faza, `intents`, checkpointul `state`, timpii și marcajul `terminal`.
`intents["limit:BUY"]`/`intents["limit:SELL"]` păstrează lifecycle-ul canonic;
`state.tickets` și `state.snapshots` păstrează ledgerul financiar al coordonatorului.
Scrierea folosește file lock, fișier temporar, `fsync` și `os.replace`.

## Ce înseamnă reconciliere

Reconcilierea nu înseamnă „presupunem că ordinul a reușit” și nici simpla comparare a
soldului. Este corelarea deterministă a patru surse:

1. intenția persistentă (`intent_id` și `client_order_id`);
2. ordinul venue-ului (`order_id`, open orders și lookup după client ID);
3. statusul normalizat și fillurile cumulative (`filled_qty`, cost, fee);
4. state-ul financiar al strategiei și execution audit.

Dacă `order_id` lipsește, se face lookup după `client_order_id`. Dacă ordinul există,
se persistă ID-ul și se citește statusul. Dacă este open/partial, rămâne urmărit fără
resubmit. Dacă este terminal, adevărul venue-ului rămâne în `terminal_status` până
când strategia aplică atomic delta-fill-ul în poziție și checkpoint. Dacă lookup-ul
sau statusul eșuează, starea este ambiguă și se păstrează; nu se inventează absență.
Open-order inventory detectează separat ordinele orfane care nu au owner local.

Această delimitare evită atât ordinul pierdut, cât și retrimiterea necontrolată, fără
a muta pragurile sau politica financiară într-un fallback generic.

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

## Refuz local înainte de submit versus rezultat ambiguu după submit

Nu orice apel `Instrument.place(...)` care nu întoarce un `order_id` înseamnă că
ordinul s-a pierdut. Sunt două situații diferite:

1. **Refuz local înainte de submit.** Guard-ul de profit, trendul, validarea sau
   altă politică poate opri intenția înainte ca providerul să fie apelat. Starea
   lifecycle este `submit_refused`, cu motivul exact din `outcome_context`. Nu
   există ordin pe exchange care trebuie căutat sau retransmis de worker.
2. **Răspuns pierdut/ambiguu după submit.** Providerul a putut primi ordinul, dar
   procesul nu a primit un `order_id` sigur. În acest caz se face mai întâi lookup
   după `client_order_id`; retransmiterea este permisă numai dacă reconcilierea nu
   găsește ordinul existent și politica intenției permite retry.

Pentru `rtrade`, refuzurile locale rămân în audit ca intenții terminale și nu sunt
introduse în outbox-ul generic. Perechea curentă este închisă, iar o evaluare
ulterioară poate crea o intenție nouă după backoff. Asta evită ca workerul generic
să încerce să reconstruiască în afara strategiei relația dintre BUY și SELL.

## Identificatorii rtrade

O pereche `rtrade` folosește patru identificatori cu roluri diferite:

| Câmp | Exemplu | Rol |
| --- | --- | --- |
| `pair_id` | `3110e4e9a39f4453b0acaf1d41c9537a` | Identitatea internă persistentă a perechii BUY/SELL. Este cheia principală din `cachedb/rtrade_pairs.json`. |
| `intent_id` | `rtrade:3110...:limit:buy` | Cheia internă de corelare între strategie, audit și lifecycle. Nu este trimisă exchange-ului și nu are limita Binance pentru `newClientOrderId`. |
| `client_order_id` | `RT_f3d1...` | Cheia de idempotency aleasă de noi și trimisă providerului. Prefixul `RT_` identifică ownerul, iar restul identifică determinist o singură ramură `pair_id + side + kind`. |
| `order_id` | număr alocat de venue | Identitatea returnată de exchange după acceptarea ordinului. |

În implementarea curentă, `client_order_id` este:

```text
RT_ + BLAKE2s-128(pair_id + side + kind)
```

Rezultatul are 35 de caractere și încape în limita de 36 de caractere tratată de
integrarea Binance. Hashul de 128 de biți nu este necesar pentru secretizare; este
folosit ca o cheie deterministă, compactă și cu probabilitate neglijabilă de
coliziune. Aceeași ramură produce același ID după restart, iar BUY și SELL produc
ID-uri diferite fără un contor global.

`RT` singur nu poate fi un `client_order_id`: toate ordinele rtrade ar avea aceeași
cheie, lookup-ul ar deveni ambiguu, iar providerul poate refuza reutilizarea ei.
Forma `RT_123` ar putea fi sigură numai dacă `123` vine dintr-un allocator
persistent și atomic, unic între procese și restarturi, iar valoarea este salvată
înainte de primul submit. Un contor ținut doar în memorie ar reveni la zero după
reboot și ar putea identifica greșit un ordin vechi.

Pentru lizibilitate operațională, soluția recomandată este să păstrăm ID-ul de
protocol stabil și să adăugăm separat un alias de afișare, de exemplu `R000123`,
sau să afișăm primele 8 caractere din `pair_id`. Acest alias poate apărea în loguri
și rapoarte, dar nu trebuie folosit singur pentru idempotency ori reconciliere.
Aceeași regulă se aplică `intent_id`: forma lungă rămâne cheia exactă, iar un alias
scurt este doar pentru operator.

Identificatorii deja persistați sunt imuabili. Schimbarea schemei trebuie
versionată și migrată doar când nu există ordine sau intenții în tranziție;
altfel, lookup-ul după vechiul `client_order_id` poate rata tocmai ordinul pe care
reconcilierea încearcă să-l protejeze.

## Reconcilierea rtrade după restart

La pornire, `rtrade` citește `cachedb/rtrade_pairs.json` și tratează fiecare
înregistrare neterminală ca stare de recuperat, nu ca motiv pentru un submit nou.
Pentru fiecare ramură se corelează:

1. `pair_id` și `intent_id` din starea strategiei;
2. `client_order_id` din lifecycle/audit;
3. `order_id` și statusul normalizat din cache-ul providerului sau din lookup-ul
   live;
4. cantitatea cerută, executată și rămasă, inclusiv fill-uri parțiale și fee-uri;
5. starea terminală a ramurii și efectul ei asupra perechii BUY/SELL.

Doar după această corelare se decide una dintre acțiunile terminale: urmărește
ordinul existent, aplică fill-ul în strategia persistentă, retransmite aceeași
intenție conform politicii ei sau închide intenția refuzată/anulată. Rebootul nu
trebuie să creeze un `pair_id`, `intent_id` sau `client_order_id` nou pentru o
ramură deja emisă.

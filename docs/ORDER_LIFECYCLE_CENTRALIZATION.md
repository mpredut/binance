# Centralizarea OrderLifecycle

## Decizia curentă

`order_retry.py` este sursa canonică pentru mecanica reutilizabilă a ciclului de
viață al ordinelor. Centralizarea privește mecanica de execuție, nu deciziile
financiare ale strategiilor.

Există două moduri de folosire a aceluiași domeniu:

1. outbox global: `Instrument.place` persistă înainte de submit, iar
   `order_retry_worker.py` este consumatorul unic care reia și urmărește recordurile;
2. lifecycle cu state deținut de strategie: strategia apelează sincron
   `order_retry.TrackedOrderLifecycle.submit/reconcile`, furnizând callback-ul prin
   care intenția este persistată atomic în campania sa.

`TrackedOrderLifecycle` nu este și nu pornește un proces separat. Este un state
machine apelat o dată pe tick de AssetGuardian, spot-DCA și trailing. Procesul
continuu al flotei rămâne `order_retry_worker.py` și consumă numai outbox-ul global.
Workerul este un proces OS separat, nu un thread pornit de `Instrument.place`.
Plasarea nu face polling și nu așteaptă terminalul: se încheie după persistență,
garduri și un singur apel de submit către provider.

Fișierul `providers/tracked_order.py` este doar un shim temporar de compatibilitate.
Codul de producție importă tipurile lifecycle direct din `order_retry`.

## Ce este centralizat

- validarea identității `intent_id` / `client_order_id`;
- persistența înainte de submit;
- delimitarea dintre acceptare și fill;
- recuperarea unui răspuns ambiguu prin client order ID;
- statusul `open`, partial și terminal;
- tranzițiile persistente ale outbox-ului dintr-un snapshot de status;
- păstrarea statusului nativ al venue-ului;
- confirmările multiple de absență;
- cancel-ul bounded, persistat înainte de efectul extern;
- auditul mecanic al etapelor lifecycle;
- adaptorul pentru un `StrategyExecutor` credential-scoped.

## Ce rămâne în strategie

- semnalul și validitatea sa;
- campania, ciclul, tierul și bugetul;
- contabilizarea delta-fill-urilor și P&L;
- aplicarea fill-ului în poziție;
- decizia dacă un terminal permite o intenție nouă;
- diferența dintre ENTRY, DCA, TP și ieșire protectoare;
- dacă un cancel invalidează intenția sau reprezintă repricing;
- dacă o ieșire poate trece de la LIMIT la MARKET.

O strategie folosește `caller_owns_retry=True` când își păstrează intenția în
propriul state și apelează lifecycle-ul central. Acest flag nu înseamnă că strategia
reinventează mecanica; înseamnă că outbox-ul global nu are voie să creeze ulterior o
intenție după ce strategia și-a invalidat campania.

## Inventar după centralizarea codului

| Cale | Motor lifecycle | Persistență activă |
|---|---|---|
| `tradeall`, `monitortrades`, comenzi prin `Instrument.place` | outbox + worker din `order_retry` | `cachedb/order_retry_queue.jsonl` |
| AssetGuardian BUY/SELL per asset | `order_retry.TrackedOrderLifecycle` | campania AssetGuardian |
| Binance/Kraken trailing | `order_retry.TrackedOrderLifecycle` | state trailing |
| Kraken/Hyperliquid spot-DCA | `order_retry.TrackedOrderLifecycle` plus contabilizare DCA | state-ul strategiei |
| rtrade pair | coordonator propriu; încă nemigrat complet | `pair_store` |
| T212 strategy | lifecycle propriu bazat pe ordine active și delta poziției | state T212 |
| T212 one-shot | reconciliere proprie fără client-ID universal | marker profil+ticker |
| `monitororder` | nelive; nu se mai extinde | exclus din migrarea următoare |

## Capabilități de reconciliere declarate

Adaptorii nu mai pot lăsa lifecycle-ul să deducă suportul din existența accidentală
a unei metode sau dintr-un `[]` moștenit. `OrderReconciliationCapabilities` declară
strict operațiile normalizate disponibile; lipsa declarației înseamnă lipsă de suport.

| Venue | lookup client ID | status order ID | cancel order ID | listă open orders |
|---|---:|---:|---:|---:|
| Binance | da | da | da | da |
| Kraken | da | da | da | da |
| Hyperliquid spot | da | da | da | da |
| Trading212 | nu | da | da | nu, reconcilierea rămâne order+portfolio |

`false` nu înseamnă că venue-ul nu are niciun endpoint posibil; înseamnă că adaptorul
comun nu oferă momentan o operație suficient de strictă pentru lifecycle. Erorile de
transport rămân distincte de o capabilitate absentă.

## De ce nu mutăm încă toate state-urile într-un singur fișier

Un ledger unic fără politica terminală ar putea retrimite un BUY după expirarea
semnalului sau ar putea pierde o ieșire protectoare după un cancel extern. Mai întâi
centralizăm codul mecanic. Politicile financiare și migrarea autorității de state vor
fi o etapă separată, protejată prin teste de caracterizare.

T212 necesită în plus capabilități de recuperare diferite: ordine active și delta de
portofoliu, deoarece client-order-ID lookup nu este universal disponibil.

## Contractul viitor pentru politica terminală — amânat

Etapa următoare va adăuga o politică declarativă per intenție, fără a o implementa în
acest refactor. Câmpurile candidate salvate pentru analiză sunt:

```text
intent_id
origin / strategy
venue / account
symbol / side / kind
cycle / campaign / tier
requested_qty / executed_qty / remaining_qty
requested_price / budget_cap
valid_until / state_version
retry_on_absent
retry_on_rejected
retry_on_expired
retry_on_canceled
partial_fill_policy
cancel_origin
requires_strategy_revalidation
protective_exit / allow_market_fallback
```

Statusul singur nu decide retry-ul. `CANCELED` poate însemna invalidare de strategie,
repricing, operator, exchange sau o stare necunoscută. De aceea politicile financiare
nu vor fi adăugate ca fallback generic în `order_retry`.

## Pașii de refactor rămași

1. păstrăm `providers/tracked_order.py` până când nu mai există consumatori externi;
2. migrăm întâi rtrade, apoi T212, doar cu characterization/golden tests;
3. abia ulterior introducem politicile financiare declarative și un ledger activ unic.

Tranzițiile mecanice duplicate din `order_retry_worker.py` au fost extrase în
`order_retry.advance_claimed_status`. Workerul păstrează exclusiv I/O-ul cu venue-ul,
auditul și orchestrarea unei iterații; nucleul comun aplică atomic snapshotul în
outbox.

Nu se schimbă praguri, bugete, tier-uri sau semantica financiară în timpul acestei
centralizări mecanice.

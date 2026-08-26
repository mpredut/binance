# assetguardian — politică financiară și limite

`assetguardian.py` evaluează independent fiecare simbol configurat prin
`AG_SYMBOLS` (`BTCUSDC,TAOUSDC` în configurația versionată) la fiecare 54 secunde.
Toate cheile `AG_*` sunt obligatorii; lipsa sau o valoare goală/invalidă oprește
procesul la pornire. Nu există valori implicite ascunse în cod. Nu este un
stop-loss: protecția de crash aparține modulului trailing stop. Guardianul
implementează două semnale contrariene rare, calculate din prețul fiecărui activ,
nu din valoarea totală a portofoliului.

## Semnale per activ

- Growth exit în tranșe: la +15%/+25%/+35% față de minimul propriu încearcă SELL
  pe același activ cu 30%/30%/40% din cantitatea liberă capturată la începutul
  campaniei. La prima tranșă se îngheață minimul ferestrei și cantitatea inițială;
  pragurile nu se mută când fereastra mobilă avansează. Profit guard-ul SELL rămâne
  activ, deoarece creșterea față de minim nu dovedește automat profit față de cost.
- Drawdown buy în tranșe: la -7%/-10%/-14% față de maximul propriu al activului din
  ultimele 24h, încearcă BUY pe același simbol, cu 35%/35%/30% din bugetul campaniei
  acelui simbol. Bugetul inițial este 99,5% din USDC liber când începe
  campania. Submitul nu completează tranșa: numai un status terminal `closed` cu
  cantitate executată o marchează completă. Campania acelui activ se rearmează după
  recuperarea drawdown-ului sub 3%, dar niciodată cât o intenție BUY este pending.

Exemplu: o scădere TAOUSDC de la 242 la 225 este aproximativ -7,02% și poate emite
o intenție BUY TAOUSDC. Nu mai poate produce o intenție BTC doar fiindcă TAO a
scăzut. În oglindă, o creștere TAO peste prag poate vinde numai tranșa TAO
corespunzătoare, nu BTC și nu întregul portofoliu la primul prag.

`AG_SELL_REARM_GROWTH_PCT=5` se raportează la minimul înghețat, nu la un minim
mobil nou. Dacă minimul înghețat este 100, campania se rearmează când prețul revine
la cel mult 105. Starea SELL este atunci ștearsă; numai o campanie viitoare va citi
și va îngheța minimul curent din fereastra mobilă. Histereza împiedică oscilația
imediată în jurul primului prag de +15%.

Minimul și maximul vin din cache-ul comun `Price24`, iar fiecare rând este validat
pentru simbol, timestamp și preț finit pozitiv. Un baseline lipsă, stătut sau format
numai din eșantionul curent nu poate produce ordin.

## Execuție și retry

Ordinele trec prin `mkt.place`/`Instrument.place`. BUY-ul AssetGuardian setează
`bypass_profit_reference=True`: sare numai comparația cu vechea referință SELL,
fiindcă semnalul valid este drawdown-ul per activ față de propriul maxim. Noul flag
nu este `bypass_profit_guard=True`; quantity/weight policy continuă să plafoneze
cantitatea. SELL-ul setează `bypass_quantity_policy=True`, permis de nucleu numai
pentru SELL: tranșa explicită înlocuiește plafonul dinamic weight, dar nu sare
profit-reference, soldul real sau fee-cap. Rămân active plafonul zilnic, anti-spam,
cooldown-ul, reconcilierea soldului și mecanica Binance. Nici BUY, nici SELL nu
folosesc bypass-ul larg `bypass_profit_guard=True`.

`Instrument.place` nu mai folosește niciun `sleep`/polling pentru trend: gate-ul
comun face o singură verificare instantanee și, dacă refuză, întoarce imediat
controlul; outboxul comun va reîncerca într-un tick ulterior. AssetGuardian își
gestionează separat aceeași semantică prin
`AG_TREND_DEFER_MAX_SEC=180`, care persistă o amânare non-blocantă per
simbol/side/tranșă. La fiecare 15–30 s recitește trendul, prețul, semnalul, soldul și
ordinele; plasează mai devreme dacă trendul devine favorabil sau după maximum 180 s
numai dacă semnalul este încă valid. Nicio funcție de plasare nu ține procesul într-o
buclă de așteptare.

Guardianul setează `caller_owns_retry=True`: nu introduce ordine în outbox. Atât BUY,
cât și SELL folosesc componenta generică `TrackedOrderLifecycle`, disponibilă prin
`MarketApi.tracked_order_lifecycle`. Strategia persistă intenția cu
`client_order_id` determinist înainte de submit; componenta comună face lookup,
status, TTL cancel și audit provider-neutral. `mkt.place` rămâne apelul sincron de
politică/mecanică și nu așteaptă fill-ul.

`NEW` și `PARTIALLY_FILLED` rămân pending; o tranșă devine completă numai după status
terminal `closed/FILLED` cu `filled_qty>0`. Un SELL terminal parțial păstrează
cantitatea executată, iar un BUY terminal parțial păstrează costul și fee-ul real;
un ciclu ulterior poate cere numai restul tranșei. Dacă răspunsul submit se pierde,
intenția este căutată după client ID. O confirmare explicită că ordinul lipsește o
eliberează fără a completa tranșa și permite reevaluare/retry imediat în același
ciclu. Nici absența, nici o eroare de lookup nu sunt interpretate ca fill.

Pentru AssetGuardian este activată explicit politica „at least once” cerută de
operator: atât absența confirmată, cât și indisponibilitatea lookup-ului eliberează
intenția pentru revalidarea completă a strategiei și retransmitere în același/următorul
ciclu. Retransmiterea aceleiași încercări refolosește același `client_order_id`;
exchange-ul poate astfel deduplica, dar în cazul unei citiri externe fals-negative
politica acceptă riscul unei cereri duplicate în locul pierderii intenției. Nici acest
caz nu completează tranșa fără fill terminal confirmat.

Un ordin limit BUY/SELL propriu rămas deschis peste
`AG_ORDER_MAX_AGE_SEC=900` (15 minute) este reinterogat și primește maximum o
cerere de anulare. Marcajul `cancel_attempted_at` este persistat înainte de apelul
API. După cancel se cere din nou statusul: un fill parțial terminal este contabilizat
și numai restul poate fi reîncercat într-un ciclu ulterior. Dacă anularea sau
statusul sunt ambigue, intenția rămâne pending și blochează orice înlocuitor. Acest
TTL se aplică exclusiv ordinului identificat de `client_order_id` al Guardianului;
un SELL deschis de alt modul blochează Guardianul, dar nu este anulat de el.

Parametrii legacy `cancelorders=True` și `hours=...` încă apar la alți caller-i,
însă în pipeline-ul consolidat sunt doar metadate pentru politica de cantitate și
nu apelează funcția veche `cancel_orders_old_or_outlier`. Nu sunt reactivați global
prin această schimbare, pentru a nu modifica pe ascuns ownership-ul ordinelor
`rtrade`, `tradeall` sau `monitortrades`.

Un refuz sau o absență confirmată determină recalcularea semnalului, prețului,
soldului și stării, fără outbox generic. Intervalul este dinamic: 54s normal, 30s la
cel mult două puncte procentuale de următoarea tranșă și 15s cât timp o
tranșă/intenție sau amânare pe trend este activă. Nu există MARKET sau `force=True`
în acest modul.

Pentru a limita concurența pe cash-ul USDC comun, evaluatorul oprește ciclul după
primul ordin acceptat. Ordinea din `AG_SYMBOLS` decide ce semnal este evaluat primul;
următorul ciclu reevaluează toate simbolurile din starea actuală. Un ordin SELL
ori BUY acceptat nu este numit fill și nu completează tranșa fără reconcilierea
exchange.

## Stare persistentă

Starea este versiunea 3 și păstrează sub fiecare simbol campanii `buy` și `sell`
separate. BUY reține peak-ul, cash-ul inițial, costul/fee-ul executat per tranșă,
intenția pending și ordinele terminale. SELL reține
trough-ul înghețat, cantitatea inițială, tranșele completate, cantitățile executate,
intenția pending și maximum 20 de ordine terminale pentru audit. Starea v2 per
simbol și starea globală legacy sunt migrate conservator numai ca stare BUY;
legacy-ul global aparține numai simbolului istoric BTCUSDC.

## Riscuri și verdict

- BUY la drawdown este mean-reversion/catch-the-dip, nu protecție. Poate cumpăra un
  activ care continuă să cadă.
- Campaniile sunt separate, dar cash-ul este comun. Primul semnal acceptat reduce
  soldul disponibil pentru celelalte active; fiecare ordin recitește soldul real și
  este plafonat la acesta.
- Configurația permite cumulat până la 99,5% din cash-ul disponibil pentru campania
  unui activ. Tranșele reduc riscul de timing, nu riscul de concentrare.
- Guard-urile comune, în afară de referința BUY și quantity-policy SELL exceptate
  îngust și explicit, pot refuza ordinul; aceasta este comportare fail-closed.
- Profitabilitatea strategiei nu este demonstrată. Schimbarea repară atribuirea
  semnalului și ordinului la același activ, nu garantează câștig.

## Invariante operaționale

- toate cheile `AG_*` trebuie să existe și să fie valide; nu există fallback-uri;
- fiecare decizie este `preț simbol -> ordin pe același simbol`;
- baseline lipsă sau sold/preț indisponibil => fără ordin;
- rândurile cache invalide, viitoare, stătute, `NaN` sau infinite sunt ignorate;
- evaluarea citește atomic un snapshot al cache-ului, fără concurență cu threadul de
  sincronizare;
- maximum un ordin acceptat per ciclu și niciun ordin în outbox-ul global;
- aceeași tranșă nu este contabilizată de două ori; submitul ei poate fi repetat cu
  același ID după un rezultat absent/neverificabil;
- orice intenție BUY/SELL pending este reconciliată înainte de citirea unui semnal
  nou, chiar dacă feedul de preț este momentan indisponibil;
- numai ordinul pending deținut de Guardian poate primi o singură anulare la
  depășirea TTL-ului; rezultat ambiguu => pending, fără resubmit;
- un ordin SELL deschis existent blochează crearea unei campanii/ordine duplicate;
- un trigger BUY/SELL raportează submit acceptat separat de confirmarea fill-ului;
- cantitățile sunt reconciliate în pipeline-ul comun înainte de Binance;
- logurile sunt gestionate de rotația comună a flotei.

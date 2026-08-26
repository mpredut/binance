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
  campania. Fiecare tranșă acceptată este marcată persistent și nu se repetă în
  aceeași cădere. Campania acelui activ se rearmează după recuperarea drawdown-ului
  sub 3%.

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
trend-wait, cooldown, reconcilierea soldului și mecanica Binance. Nici BUY, nici
SELL nu folosesc bypass-ul larg `bypass_profit_guard=True`.

Guardianul setează `caller_owns_retry=True`: nu introduce ordine în outbox. Pentru
SELL, intenția cu `client_order_id` determinist este persistată înainte de submit.
`NEW` și `PARTIALLY_FILLED` rămân pending; o tranșă devine completă numai după
status terminal `closed/FILLED`. Un terminal parțial păstrează cantitatea executată,
iar un ciclu ulterior poate cere numai restul. Dacă răspunsul submit se pierde,
intenția este căutată după client ID; nu se emite o dublură. Două confirmări
consecutive că intenția lipsește de pe exchange o eliberează fără a completa tranșa.

Un ordin SELL limit propriu rămas deschis peste
`AG_SELL_ORDER_MAX_AGE_SEC=900` (15 minute) este reinterogat și primește maximum o
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

Pentru BUY, un refuz determină următorul ciclu să recalculeze semnalul, prețul,
soldul și starea, fără outbox generic. Intervalul este dinamic: 54s normal, 30s la
cel mult două puncte procentuale de următoarea tranșă și 15s cât timp o
tranșă/intenție este pending. Nu există MARKET sau `force=True` în acest modul.

Pentru a limita concurența pe cash-ul USDC comun, evaluatorul oprește ciclul după
primul ordin acceptat. Ordinea din `AG_SYMBOLS` decide ce semnal este evaluat primul;
următorul ciclu reevaluează toate simbolurile din starea actuală. Un ordin SELL
acceptat nu este numit fill și nu completează tranșa fără reconcilierea exchange.

## Stare persistentă

Starea este versiunea 3 și păstrează sub fiecare simbol campanii `buy` și `sell`
separate. BUY reține peak-ul, cash-ul inițial și tranșele acceptate. SELL reține
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
- aceeași tranșă nu se repetă în aceeași campanie a aceluiași simbol;
- o intenție SELL pending este reconciliată înaintea oricărei intenții noi;
- numai ordinul SELL pending deținut de Guardian poate primi o singură anulare la
  depășirea TTL-ului; rezultat ambiguu => pending, fără resubmit;
- un ordin SELL deschis existent blochează crearea unei campanii/ordine duplicate;
- un trigger SELL raportează submit acceptat separat de confirmarea fill-ului;
- cantitățile sunt reconciliate în pipeline-ul comun înainte de Binance;
- logurile sunt gestionate de rotația comună a flotei.

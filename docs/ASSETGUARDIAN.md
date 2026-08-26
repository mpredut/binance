# assetguardian — politică financiară și limite

`assetguardian.py` evaluează independent fiecare simbol configurat prin
`AG_SYMBOLS` (implicit `BTCUSDC,TAOUSDC`) la fiecare 54 secunde. Nu este un
stop-loss: protecția de crash aparține modulului trailing stop. Guardianul
implementează două semnale contrariene rare, calculate din prețul fiecărui activ,
nu din valoarea totală a portofoliului.

## Semnale per activ

- Growth exit: dacă prețul curent al unui activ este cu 100% peste minimul propriu
  din ultimele 24h, încearcă SELL numai pentru soldul liber al acelui activ. Pragul
  este intenționat practic dezactivat, deoarece testarea istorică a vânzării
  agresive a pierdut față de hold.
- Drawdown buy în tranșe: la -7%/-10%/-14% față de maximul propriu al activului din
  ultimele 24h, încearcă BUY pe același simbol, cu 35%/35%/30% din bugetul campaniei
  acelui simbol. Bugetul inițial este implicit 99,5% din USDC liber când începe
  campania. Fiecare tranșă acceptată este marcată persistent și nu se repetă în
  aceeași cădere. Campania acelui activ se rearmează după recuperarea drawdown-ului
  sub 3%.

Exemplu: o scădere TAOUSDC de la 242 la 225 este aproximativ -7,02% și poate emite
o intenție BUY TAOUSDC. Nu mai poate produce o intenție BTC doar fiindcă TAO a
scăzut. În oglindă, o creștere TAO peste prag poate vinde numai TAO, nu BTC și nu
întregul portofoliu.

Minimul și maximul vin din cache-ul comun `Price24`, iar fiecare rând este validat
pentru simbol, timestamp și preț finit pozitiv. Un baseline lipsă, stătut sau format
numai din eșantionul curent nu poate produce ordin.

## Execuție și retry

Ordinele trec prin `mkt.place`/`Instrument.place`, deci păstrează profit guard,
plafon zilnic, trend-wait, cooldown, reconcilierea soldului, fee-cap și mecanica
Binance. Un drawdown de -7% este o condiție necesară pentru prima tranșă, nu o
garanție că ordinul va fi acceptat: de exemplu, profit guard poate refuza un BUY
dacă prețul nu este suficient de bun față de ultima referință SELL.

Guardianul setează `caller_owns_retry=True`: nu introduce ordine în outbox. Dacă un
ordin este refuzat, următorul ciclu recalculează prețul, fereastra, soldul și starea,
apoi poate încerca din nou numai dacă semnalul financiar este încă adevărat.
Intervalul este dinamic: 54s normal, 30s la cel mult două puncte procentuale de
următoarea tranșă și 15s cât timp o tranșă declanșată este refuzată. Nu există
MARKET, `force=True` sau bypass în acest modul.

Pentru a limita concurența pe cash-ul USDC comun, evaluatorul oprește ciclul după
primul ordin acceptat. Ordinea din `AG_SYMBOLS` decide ce semnal este evaluat primul;
următorul ciclu reevaluează toate simbolurile din starea actuală. Un ordin acceptat
nu este numit fill: statusul real rămâne responsabilitatea pipeline-ului comun și a
reconcilierii cu exchange-ul.

## Stare persistentă

Starea este versiunea 2 și păstrează campanii separate sub cheia fiecărui simbol:
peak-ul de preț, cash-ul inițial și tranșele acceptate. O stare veche, globală, este
migrată conservator numai către simbolul implicit istoric BTCUSDC; nu este copiată
la TAO și nu poate marca artificial tranșe TAO ca executate.

## Riscuri și verdict

- BUY la drawdown este mean-reversion/catch-the-dip, nu protecție. Poate cumpăra un
  activ care continuă să cadă.
- Campaniile sunt separate, dar cash-ul este comun. Primul semnal acceptat reduce
  soldul disponibil pentru celelalte active; fiecare ordin recitește soldul real și
  este plafonat la acesta.
- Configurația permite cumulat până la 99,5% din cash-ul disponibil pentru campania
  unui activ. Tranșele reduc riscul de timing, nu riscul de concentrare.
- Guard-urile comune pot refuza ordinul; aceasta este comportare fail-closed, nu
  eroare și nu este ocolită de această schimbare.
- Profitabilitatea strategiei nu este demonstrată. Schimbarea repară atribuirea
  semnalului și ordinului la același activ, nu garantează câștig.

## Invariante operaționale

- simbolurile, intervalul, pragurile, fereastra și cash ratio trebuie să fie valide;
- fiecare decizie este `preț simbol -> ordin pe același simbol`;
- baseline lipsă sau sold/preț indisponibil => fără ordin;
- rândurile cache invalide, viitoare, stătute, `NaN` sau infinite sunt ignorate;
- evaluarea citește atomic un snapshot al cache-ului, fără concurență cu threadul de
  sincronizare;
- maximum un ordin acceptat per ciclu și niciun ordin în outbox-ul global;
- aceeași tranșă nu se repetă în aceeași campanie a aceluiași simbol;
- starea persistentă nu păstrează o intenție executabilă fără reevaluarea semnalului;
- un trigger raportează succes numai dacă pipeline-ul a acceptat ordinul;
- cantitățile sunt reconciliate în pipeline-ul comun înainte de Binance;
- logurile sunt gestionate de rotația comună a flotei.

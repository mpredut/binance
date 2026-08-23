# assetguardian — politică financiară și limite

`assetguardian.py` evaluează valoarea totală a portofoliului Binance în USDC la
fiecare 54 secunde. Nu este un stop-loss: protecția de crash aparține modulului
trailing stop. Guardianul implementează două semnale contrariene rare.

## Semnale

- Growth exit: dacă valoarea curentă este cu 100% peste minimul ultimelor 24h,
  încearcă SELL pentru activele urmărite. Pragul este intenționat practic dezactivat,
  deoarece testarea istorică a vânzării agresive a pierdut față de hold.
- Drawdown buy: dacă valoarea curentă este cu 7% sub maximul ultimelor 24h, încearcă
  să cumpere simbolul implicit (`BTCUSDC`) folosind 99,5% din USDC liber.

Minimul și maximul sunt calculate din aceeași fereastră, dar au roluri financiare
diferite: profitul față de trough și drawdown-ul față de peak. Citirea acceptă cheia
istorică `total_value_usdt` doar pentru compatibilitatea cache-ului vechi; toate
deciziile și ordinele operaționale sunt USDC.

## Execuție și retry

Ordinele trec prin `mkt.place`/`Instrument.place`, deci păstrează profit guard,
plafon zilnic, trend-wait, cooldown, reconcilierea soldului, fee-cap și mecanica
Binance. Guardianul setează `caller_owns_retry=True`: nu introduce ordine în outbox.
Dacă un ordin este refuzat, următorul ciclu recalculează portofoliul și poate încerca
din nou numai dacă semnalul financiar este încă adevărat.

SELL folosește numai soldul liber; activele blocate în ordine nu sunt atinse. Ordinul
este LIMIT/safe, nu lichidare MARKET garantată, iar quantity policy poate reduce mult
cantitatea. Numele istoric `sell_all_assets` descrie intenția, nu garantează vânzarea
întregii poziții într-un singur ciclu.

## Riscuri și verdict

- BUY la drawdown este mean-reversion/catch-the-dip, nu protecție. Poate cumpăra într-o
  piață care continuă să cadă.
- 99,5% din cash produce concentrare foarte mare în BTC; valoarea este configurabilă
  prin `AG_BUY_USE_CASH_RATIO` și trebuie redusă sau împărțită în tranșe înainte de
  folosirea guardianului ca strategie principală.
- Semnalul se bazează pe valoarea întregului portofoliu, dar cumpără un singur activ.
  O scădere produsă de TAO poate declanșa cumpărare BTC; aceasta este o alegere de
  alocare, nu o compensare exactă a activului care a scăzut.
- Guard-urile comune pot refuza ordinul; aceasta este comportare fail-closed, nu eroare.

Modulul poate favoriza cumpărarea unui drawdown și realizarea unui câștig excepțional,
dar profitabilitatea nu este demonstrată. Principalul beneficiu al corecțiilor este că
semnalul este calculat coerent și nu supraviețuiește artificial prin retry-uri fantomă.

## Invariante operaționale

- intervalul, pragurile, fereastra și cash ratio trebuie să fie valori valide;
- baseline lipsă sau sold/preț indisponibil => fără ordin;
- rândurile cache/balanță invalide, viitoare, `NaN` sau infinite sunt ignorate;
- evaluarea citește atomic un snapshot al cache-ului, fără concurență cu threadul de sync;
- maximum o evaluare per ciclu; fără stare de retry în memorie sau pe disc;
- un trigger raportează succes numai dacă pipeline-ul a acceptat cel puțin un ordin;
- toate cantitățile sunt reconciliate în pipeline-ul comun înainte de Binance;
- logurile sunt gestionate de rotația comună a flotei.

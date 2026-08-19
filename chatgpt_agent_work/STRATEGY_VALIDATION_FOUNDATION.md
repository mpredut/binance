# Fundația de validare înaintea optimizării strategiilor

Data: 2026-08-19

## Ce este blocat înainte de tuning

Nicio configurație nu este promovată doar pentru că maximizează profitul brut pe
aceeași perioadă pe care a fost aleasă. Înainte de schimbarea parametrilor cerem:

1. același engine de decizie în live și replay;
2. stare inițială curată și ordine decise la close executabile abia din bara următoare;
3. P&L net cu fiecare fee taxat o singură dată și cost basis corect la partial fills;
4. train/validation/test strict temporale prin walk-forward;
5. comparație cu strategia live curentă și buy-and-hold;
6. return, max drawdown, timp sub apă, Sharpe, Sortino, Calmar, CVaR95,
   expunere, turnover, profit factor și expectancy;
7. rezultate out-of-sample pe mai multe regimuri și costuri conservatoare.

## Implementare

- `offline/backtests/metrics.py`: metrici pure din equity mark-to-market. Metricile
  anualizate sunt indisponibile dacă timeframe-ul nu este declarat; nu inventăm o
  anualizare pentru serii cu frecvență necunoscută.
- `offline/backtests/walk_forward.py`: fold-uri rolling sau anchored fără shuffle și
  fără suprapunere train/validation/test în interiorul aceluiași fold.
- `offline/backtests/datasets.py`: contract OHLC, validare, serializare canonică și
  hash identice pentru toate venue-urile.
- `offline/backtests/evaluation.py`: evaluarea segmentelor și agregarea walk-forward
  sunt comune; engine-ul financiar intră prin adaptor.
- `kraken/replay.py`: pornește din stare explicită, ignoră orice `.state_REPLAY`,
  execută ordinul cel mai devreme în bara următoare și raportează metricile comune.
- `212trading/replay.py`: rulează `212trading/strategy.py` live peste OHLC și
  modelează fill-urile separat. Gate-ul DCA este acceptat numai pe bare 5m,
  aceeași cadență ca Yahoo live.
- `offline/runners/t212_walk_forward_baseline.py`: încarcă direct
  `config.<profile>.env`; nu există o copie generică a parametrilor live.
- `offline/runners/run_financial_baseline.sh`: poarta rapidă obligatorie înainte și
  după orice modificare de strategie/risk/execution.

## Defecte financiare reparate în timpul construirii baseline-ului

1. Trailing TP v2 se dezarma când prețul revenea sub nivelul TP. Pullback-ul putea
   deschide DCA în loc să închidă poziția. După armare, vârful rămâne activ până la
   ieșire, iar tick-ul de exit nu poate cumpăra simultan.
2. La SELL parțial, costul întreg rămânea pe cantitatea reziduală, deformând media.
3. `cycle_fees` era scăzut din nou la fiecare tranșă. Acum fiecare fill plătește fee
   o singură dată, inclusiv BUY-ul unei poziții încă deschise.
4. Trading212 paper/replay păstra costul întreg după un SELL parțial din ladder,
   umflând media poziției rămase. Cost basis-ul scade acum proporțional.
5. Trading212 paper nu arma re-buy-ul după un stop, deși calea reală îl arma.
   Ambele căi folosesc acum aceeași tranziție financiară.

## Ce nu este încă suficient pentru promovare

- OHLC nu spune ordinea intrabar high/low; barele în care s-ar putea umple ordine
  opuse trebuie stresate cu ambele ordini posibile sau cu date mai granulare.
- Slippage, spread variabil, partial-fill asincron, ordine respinse și latency nu sunt
  încă modelate complet în replay-ul Kraken.
- Replay-ul Trading212 modelează ordinele limită pe OHLC, dar nu poate reproduce
  lichiditatea T212, programul exact al sesiunii, fill-uri parțiale asincrone sau FX
  istoric fără un dataset separat de curs.
- Istoricul Kraken obținut direct din API este scurt și ferestrele raportate anterior
  se suprapun. El poate genera ipoteze, nu dovadă robustă de promovare.
- Ramura experimentală `kraken-trail-decay-v3` nu este îmbinată. Metricile ei inițiale
  foloseau diferențe absolute de P&L fără capital/frecvență corectă și un downside
  deviation care putea raporta Sortino 0 pe pierderi constante.

Datasetul HYPE lung este înghețat cu manifest și hash. Următorul pas sigur este
înghețarea dataseturilor Yahoo per profil Trading212 și compararea unor candidați
preînregistrați cu baseline-ul live, fără a amesteca strategiile Binance.

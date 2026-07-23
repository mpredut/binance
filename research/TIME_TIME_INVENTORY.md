# Inventar `time.time()` — flota + boti (23 iul 2026)

Pas 0 al planului din `UNIFIED_BACKTEST_PLAN.md`: gasit TOATE referintele la
`time.time()` din flota (tradeall/monitortrades/rtrade/assetguardian) si boti
(kraken/hyperliquid/212trading), clasificate dupa ce inseamna sa le elimini —
folosind timpul care vine ODATA CU PRETUL (timestamp-ul tick-ului/barei), sau
injectat din exterior (un `Clock`, ca `_SimClock` din tradeall_backtest.py).

Legenda:
- 🔴 **DECIZIE** — afecteaza CE se tranzactioneaza/CAND. Trebuie injectat pt
  backtest fidel.
- 🟡 **INFRA-LIVE** — cache/rate-limit/polling REAL catre exchange. Nu are
  sens sa devina "timp simulat" — n-are ce sa simuleze (nu exista in
  backtest, unde nu se bate reteaua deloc).
- 🟢 **DEJA REZOLVAT** — codul care il contine e deja INLOCUIT complet in
  backtest (monkeypatch/bypass), nu apelat vreodata acolo.
- ⚠️ **NU e doar injectare** — dependinta de timp REAL e mai profunda decat
  un parametru (bucla blocanta pe threading.Event, wait sincron pe retea).

---

## FLOTA

### `tradeall.py` (4 aparitii, dar doar 1 e o problema reala)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 107 | `log_decision()`: `cols=[time.time(), ...]` — scrie in jurnalul de decizii | 🟢 | `tradeall_backtest.py` inlocuieste FUNCTIA INTREAGA (`ta.log_decision = make_decision_logger(out_dir, clock)`) — asta de aici nu ruleaza NICIODATA in backtest. |
| 613 | `handle_symbol()`: `"ts": time.time()` in snapshot-ul intors | 🔴 | Feed-uieste `shadow.update(symbol, snapshot["ts"], ...)` → Kalman foloseste `dt = ts - last_ts` pt scalarea zgomotului de proces. **Azi bypassed** (backtest nu cheama `handle_symbol()`, isi construieste propriul flux) — dar daca vreodata codul REAL `handle_symbol()` ruleaza pe replay (planul de unificare), acest `ts` TREBUIE sa vina din timestamp-ul pretului replay-uit, nu din wall-clock, altfel Kalman calculeaza dt gresit (amesteca timp real cu date istorice). |
| 711 | `TrendCoordinator._is_due()`: `self._last_eval[symbol] = time.time()` | 🔴 | Gateaza CAND se re-evalueaza un simbol (throttling min/max interval). Bypassed azi (backtest nu cheama `evaluate()` prin coordonator). Injectabil simplu — un `now_fn` in loc de `time.time()` direct. |
| 768 | `TrendCoordinator.run()`: `now = time.time()` in bucla principala | ⚠️ | **NU e doar injectare** — linia de DEASUPRA e `self._event.wait(timeout=self.max_interval)`, o ASTEPTARE REALA pe un `threading.Event`. Ca sa ruleze pe replay in fast-forward, bucla asta ar trebui INLOCUITA (nu doar cu ceas injectat), altfel un backtest de 329 zile ar dura literalmente 329 zile. |

**Concluzie tradeall.py**: azi, NIMIC din tabelul de mai sus blocheaza
backtest-ul (tot ce conteaza e deja bypassed/inlocuit). Devine relevant DOAR
daca planul de unificare ajunge sa refoloseasca `handle_symbol()`/
`TrendCoordinator` insele (nu o bucla separata) — caz in care linia 768 e
obstacolul real (redesign, nu parametru), 613 e simplu (parametru), 107/711
sunt deja rezolvate prin tiparul de substituire.

### `monitortrades.py` (2 aparitii, ambele DECIZIE)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 294 | `get_relevant_trade()`: `current_time_s = int(time.time())` | 🔴 | `can_trade = current_time_s - trade_time < threshold_s` — blocheaza un trade nou daca ultimul a fost prea recent. Direct in calea de decizie. |
| 459 | `monitor_price_and_trade()`: `current_time_s = int(time.time())` | 🔴 | Cooldown HARD-TP (`current_time_s - _hard_tp_last.get(symbol,0) >= hard_tp_cd`) + fereastra "trade prea recente" (MT_ALL_TRADES_BLOCK_SEC). |

**Concluzie monitortrades.py**: EXACT 2 puncte de injectat, ambele simple
(comparatii aritmetice pe un int, nicio bucla blocanta implicata) — cel mai
tractabil modul din toata flota pt Faza 1, confirma alegerea din
`UNIFIED_BACKTEST_PLAN.md` §7.

### `rtrade.py` (0 aparitii directe — caz special)

Grep confirma: NICIUN `time.time()`. Rtrade NU citeste "acum" nicaieri
direct — temporalitatea lui e DELEGATA integral catre raspunsurile API:
`api.check_order_filled_by_time("BUY", symbol, time_back_in_seconds=WAIT_FOR_ORDER)`
intreaba EXCHANGE-UL "a fost umplut in ultimele X secunde?", nu compara
`time.time()` local cu un timestamp retinut. Asta inseamna ca a face rtrade
testabil pe replay NU e o chestiune de injectat un Clock — ar trebui simulat
RASPUNSUL acelor apeluri API (`check_order_filled`, `check_order_filled_by_time`,
`cancel_order`) intr-un broker fals, ca `BacktestBroker`. Confirma inca un
motiv (pe langa BUY/SELL concurent pe thread-uri, deja notat in plan) ca
rtrade e o provocare diferita, NU doar "acelasi tipar, alt fisier" — ramane
justificat sa fie Faza 2.

### `assetguardian.py` (1 aparitie, DECIZIE)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 51 | `_get_value_minutes_ago_from_cache()`: `now_ts = int(time.time())` | 🔴 | `target_ts = now_ts - minutes_back*60` — fereastra de referinta pt calculul cresterii/scaderii portofoliului. Simplu de injectat (o comparatie aritmetica), dar valoare de backtest mica (vezi `BACKTEST_CANDIDATES.md` — AG_TARGET_GROWTH_PCT e deliberat "practic oprit"). Ramane Faza 2 nu din cauza dificultatii, ci a valorii.

---

## BOTI (pe pozitie: kraken, hyperliquid, 212trading)

Tipar IDENTIC repetat in toate 3: la plasarea unui ordin, se retine
`"ts": time.time()`; mai tarziu, `age = (time.time() - ts) / 60` decide daca
ordinul a stat prea mult (order-TTL, repreteaza/anuleaza). Plus cooldown-uri
similare (`buy_backoff_until`, `_dca_gate_until`, `cooldown_until`). Odata
proiectat tiparul de injectare pt UNUL (recomand kraken/strategy.py, cel mai
investigat azi), celelalte doua se aliniaza aproape mecanic — SUNT structural
identice, nu 3 probleme diferite.

### `kraken/strategy.py` — 4 aparitii, toate DECIZIE

| Linie | Context | Nota |
|---|---|---|
| 212, 219 | `"ts": time.time()` la plasarea unui ordin (`open_orders`) | Folosit la linia 263 pt order-TTL (`STRAT_ORDER_TTL_MIN`, reprice/anulare). |
| 263 | `age = (time.time() - o.get("ts",0)) / 60` | Decizia de reprice/anulare a unui ordin neexecutat. |
| 442 | `self._shadow_prices.append((time.time(), price))` | Alimenteaza `_shadow_vol_1h()` → pragul de reintrare ADAPTIV, PROMOVAT LA BANI REALI azi-sesiune (`STRAT_REENTRY_ADAPTIVE=true`). Cel mai important din tot inventarul boti — orice viitor backtest pe strategia REALA (nu `kraken/backtest.py::simulate()`, care e o alta paradigma pe bare OHLC) trebuie sa injecteze timpul aici corect, altfel volatilitatea calculata e falsa. |

**Nota metodologica**: `kraken/backtest.py::simulate()` (motorul "pozitie" de
azi) NU foloseste deloc `kraken/strategy.py` — e o reimplementare separata pe
bare OHLC (deja documentat in `UNIFIED_BACKTEST_PLAN.md` §1). Randurile de
mai sus conteaza DOAR daca planul evolueaza spre "codul REAL al strategiei
ruleaza pe replay" (facada unificata, §6 din plan) — nu schimba nimic in
`simulate()` de azi.

### `hyperliquid/strategy.py` + `delta_neutral.py` + `signals.py` — 7 aparitii DECIZIE

Acelasi tipar (ts la ordin + age la citire) in `strategy.py:164,170,227`.
`delta_neutral.py` adauga: `opened_ts`/`opened_at` (varsta pozitiei DN),
`cooldown_until` (anti-thrash intre rebalansari) — liniile 272,315,487,495.
`signals.py:62` — staleness generic (`if time.time()-ts > max_age`). Fara
motor de backtest propriu azi (spre deosebire de kraken) — ar avea nevoie de
unul nou, dupa tiparul kraken, daca se decide sa se testeze DN-ul.

### `212trading/strategy.py` + `market_data.py` — 10 aparitii DECIZIE

Acelasi tipar de order-TTL (liniile 301,308,323,329,531) + cooldown-uri
specifice (`buy_backoff_until`:313,677; `locked_zero_until`:343,473;
`_dca_gate_until`:744,748) + staleness pe date de piata
(`market_data.py:115,125` — `age_sec`/`series_age`). Fara motor de backtest
propriu azi.

### Infra-live (NU au sens sa devina "timp simulat")

- `kraken_cachemanager.py` (109,119,190), `kraken_client.py` (54,61),
  `kraken_xstock_watch.py` (97), `hl_client.py` (237),
  `212trading/order_manager.py` (71,73), `hyperliquid/dn_bot.py` (44) —
  toate sunt fie (a) parametri pt apeluri REALE catre API-ul exchange-ului
  (fereastra de lookback, cache TTL local), fie (b) o bucla de asteptare
  SINCRONA pe un raspuns real de retea. Niciunul nu exista "in timpul"
  unui backtest (care nu bate reteaua deloc) — nu au ce sa fie injectate CU.

### Fisiere de test (`hyperliquid/test_dn.py`, `212trading/test_launch_detect.py`)

Folosesc `time.time()` ca sa construiasca fixtures (nu cod de productie).
Daca `delta_neutral.py`/codul din `212trading` primesc un Clock injectabil,
aceste teste ar putea trece la randul lor pe un ceas fals in loc de
`time.time() - X` — imbunatatire de determinism al testelor, dar NU
blocheaza planul de backtest (sunt teste, nu cod care ruleaza in backtest).

---

## Rezumat — ce e cu adevarat de facut in Faza 1 (tradeall + monitortrades)

| Modul | Aparitii DECIZIE reale de injectat azi | Complexitate |
|---|---|---|
| `tradeall.py` | 0 (tot ce conteaza e deja bypassed in backtest) — devine 2 (613 simplu, 768 redesign) DOAR daca se reutilizeaza `handle_symbol`/`TrendCoordinator` direct | Mica azi, medie daca se extinde |
| `monitortrades.py` | 2 (liniile 294, 459) | Mica — 2 comparatii aritmetice |

Concluzie: **monitortrades.py e de fapt mai simplu de injectat decat
tradeall.py** in sensul strict (2 puncte clare, fara bucle blocante) — dar
tradeall.py are deja infrastructura de replay (PriceWindow/TrendState cu
`now_fn`) construita si validata azi, doar neexpusa generic. Cele doua
raman candidatii corecti pt Faza 1, din motive complementare.

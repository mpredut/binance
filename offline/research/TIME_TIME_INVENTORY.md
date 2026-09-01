# Inventar `time.time()` — flota + boti (23 iul 2026)

Step 0 of the plan in `UNIFIED_BACKTEST_PLAN.md`: find ALL references to
`time.time()` in the fleet (tradeall/monitortrades/rtrade/assetguardian) and the bots
(kraken/hyperliquid/212trading), clasificate dupa ce inseamna sa le elimini —
using the time that arrives WITH THE PRICE (the tick's or bar's timestamp), or
injected from outside (a `Clock`, like `_SimClock` in offline/backtests/tradeall.py).

Legenda:
- 🔴 **DECISION** — it affects WHAT is traded and WHEN. It must be injected for
  backtest fidel.
- 🟡 **LIVE INFRA** — cache, rate limit or REAL polling towards the exchange. It has no
  sens sa devina "timp simulat" — n-are ce sa simuleze (nu exista in
  backtest, unde nu se bate reteaua deloc).
- 🟢 **DEJA REZOLVAT** — codul care il contine e deja INLOCUIT complet in
  backtest (monkeypatch/bypass), nu apelat vreodata acolo.
- ⚠️ **NOT just injection** — the dependency on REAL time runs deeper than
  un parametru (bucla blocanta pe threading.Event, wait sincron pe retea).

---

## FLOTA

### `tradeall.py` (4 aparitii, dar doar 1 e o problema reala)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 107 | `log_decision()`: `cols=[time.time(), ...]` — writes to the decision journal | 🟢 | `offline/backtests/tradeall.py` replaces the WHOLE FUNCTION (`ta.log_decision = make_decision_logger(out_dir, clock)`), so this one NEVER runs in a backtest. |
| 613 | `handle_symbol()`: `"ts": time.time()` in the returned snapshot | 🔴 | It feeds `shadow.update(symbol, snapshot["ts"], ...)`, and Kalman uses `dt = ts - last_ts` to scale the process noise. **Bypassed today** (the backtest does not call `handle_symbol()`, it builds its own flow) — but if the REAL `handle_symbol()` code ever runs on a replay (the unification plan), this `ts` MUST come from the replayed price timestamp rather than the wall clock, otherwise Kalman computes dt wrongly (mixing real time with historical data). |
| 711 | `TrendCoordinator._is_due()`: `self._last_eval[symbol] = time.time()` | 🔴 | It gates WHEN a symbol is re-evaluated (min/max interval throttling). Bypassed today (the backtest does not call `evaluate()` through the coordinator). Simple to inject — a `now_fn` instead of calling `time.time()` directly. |
| 768 | `TrendCoordinator.run()`: `now = time.time()` in the main loop | ⚠️ | **NOT just injection** — the line ABOVE is `self._event.wait(timeout=self.max_interval)`, a REAL wait on a `threading.Event`. To run on a fast-forward replay, this loop would have to be REPLACED (not merely given an injected clock), otherwise a 329-day backtest would literally take 329 days. |

**Concluzie tradeall.py**: azi, NIMIC din tabelul de mai sus blocheaza
backtest-ul (tot ce conteaza e deja bypassed/inlocuit). Devine relevant DOAR
daca planul de unificare ajunge sa refoloseasca `handle_symbol()`/
`TrendCoordinator` itself (not a separate loop) — in which case line 768 is
obstacolul real (redesign, nu parametru), 613 e simplu (parametru), 107/711
are already solved by the substitution pattern.

### `monitortrades.py` (2 aparitii, ambele DECIZIE)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 294 | `get_relevant_trade()`: `current_time_s = int(time.time())` | 🔴 | `can_trade = current_time_s - trade_time < threshold_s` — blocheaza un trade nou daca ultimul a fost prea recent. Direct in calea de decizie. |
| 459 | `monitor_price_and_trade()`: `current_time_s = int(time.time())` | 🔴 | Cooldown HARD-TP (`current_time_s - _hard_tp_last.get(symbol,0) >= hard_tp_cd`) + fereastra "trade prea recente" (MT_ALL_TRADES_BLOCK_SEC). |

**Concluzie monitortrades.py**: EXACT 2 puncte de injectat, ambele simple
(comparatii aritmetice pe un int, nicio bucla blocanta implicata) — cel mai
the most tractable module in the whole fleet for Phase 1, confirming the choice in
`UNIFIED_BACKTEST_PLAN.md` §7.

### `rtrade.py` (0 aparitii directe — caz special)

Grep confirms: NOT ONE `time.time()`. Rtrade never reads "now" anywhere
direct — temporalitatea lui e DELEGATA integral catre raspunsurile API:
`api.check_order_filled_by_time("BUY", symbol, time_back_in_seconds=WAIT_FOR_ORDER)`
intreaba EXCHANGE-UL "a fost umplut in ultimele X secunde?", nu compara
`time.time()` local cu un timestamp retinut. Asta inseamna ca a face rtrade
testabil pe replay NU e o chestiune de injectat un Clock — ar trebui simulat
RASPUNSUL acelor apeluri API (`check_order_filled`, `check_order_filled_by_time`,
`cancel_order`) intr-un broker fals, ca `BacktestBroker`. Confirma inca un
motiv (pe langa BUY/SELL concurent pe thread-uri, deja notat in plan) ca
rtrade is a different challenge, NOT merely "the same pattern in another file" — it stays
justificat sa fie Faza 2.

### `assetguardian.py` (1 aparitie, DECIZIE)

| Linie | Context | Categorie | Nota |
|---|---|---|---|
| 51 | `_get_symbol_window_extrema()`: `now_ts = float(time.time())` | 🔴 | `target_ts = now_ts - minutes_back*60` — the per-asset window for the low and the high. SELL uses `AG_SELL_TIERS` and freezes the campaign low at the first tranche; BUY uses the per-asset high.

---

## BOTI (pe pozitie: kraken, hyperliquid, 212trading)

The SAME pattern repeated in all three: when an order is placed, it records
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
| 442 | `self._shadow_prices.append((time.time(), price))` | It feeds `_shadow_vol_1h()` and therefore the ADAPTIVE re-entry threshold, PROMOTED TO REAL MONEY in this session (`STRAT_REENTRY_ADAPTIVE=true`). The most important entry in the whole bot inventory — any future backtest of the REAL strategy (not `kraken/backtest.py::simulate()`, which is a different paradigm over OHLC bars) must inject the time correctly here, otherwise the computed volatility is false. |

**Nota metodologica**: `kraken/backtest.py::simulate()` (motorul "pozitie" de
today) does NOT use `kraken/strategy.py` at all — it is a separate reimplementation over
bare OHLC (deja documentat in `UNIFIED_BACKTEST_PLAN.md` §1). Randurile de
above matters ONLY if the plan evolves towards "the REAL strategy code
runs on the replay" (the unified facade, §6 of the plan) — it changes nothing in
`simulate()` de azi.

### `hyperliquid/strategy.py` + `delta_neutral.py` + `signals.py` — 7 aparitii DECIZIE

Acelasi tipar (ts la ordin + age la citire) in `strategy.py:164,170,227`.
`delta_neutral.py` adauga: `opened_ts`/`opened_at` (varsta pozitiei DN),
`cooldown_until` (anti-thrash intre rebalansari) — liniile 272,315,487,495.
`signals.py:62` — staleness generic (`if time.time()-ts > max_age`). Fara
motor de backtest propriu azi (spre deosebire de kraken) — ar avea nevoie de
a new one, following the Kraken pattern, if DN is ever chosen for testing.

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
  they are all either (a) parameters for REAL calls to the exchange API
  (fereastra de lookback, cache TTL local), fie (b) o bucla de asteptare
  SINCRONA pe un raspuns real de retea. Niciunul nu exista "in timpul"
  a backtest (which never touches the network) — there is nothing to inject them WITH.

### Fisiere de test (`hyperliquid/test_dn.py`, `212trading/test_launch_detect.py`)

Folosesc `time.time()` ca sa construiasca fixtures (nu cod de productie).
If `delta_neutral.py` and the `212trading` code receive an injectable Clock,
aceste teste ar putea trece la randul lor pe un ceas fals in loc de
`time.time() - X` — imbunatatire de determinism al testelor, dar NU
block the backtest plan (they are tests, not code that runs inside a backtest).

---

## Rezumat — ce e cu adevarat de facut in Faza 1 (tradeall + monitortrades)

| Modul | Aparitii DECIZIE reale de injectat azi | Complexitate |
|---|---|---|
| `tradeall.py` | 0 (everything that matters is already bypassed in the backtest) — it becomes 2 (613 simple, 768 a redesign) ONLY if `handle_symbol`/`TrendCoordinator` are reused directly | Small today, medium if it grows |
| `monitortrades.py` | 2 (liniile 294, 459) | Mica — 2 comparatii aritmetice |

Concluzie: **monitortrades.py e de fapt mai simplu de injectat decat
tradeall.py** in sensul strict (2 puncte clare, fara bucle blocante) — dar
tradeall.py are deja infrastructura de replay (PriceWindow/TrendState cu
`now_fn`) built and validated today, merely not exposed generically. The two
raman candidatii corecti pt Faza 1, din motive complementare.

# Unificare provider-agnostic a motorului de strategie (Calea B)

**Scop:** motorul base v2 (DCA+TP+trailing, validat live pe HYPE) să fie independent
de Kraken și să ruleze prin același contract pe orice venue compatibil, cu o singură
suită de conformitate și fără a forța strategiile diferite T212/HL în același algoritm.

**De ce B (nu adaptor):** mai puțin cod (nu apare o a 3-a abstracție) și mai testabil
(o suită parametrizată peste toți providerii). Motorul rămâne pe contractul strict
`StrategyExecutor`; nu este rutat prin `MarketApi.place`, ale cărui guardrail-uri nu
fac diferența între intrări și ieșirile urgente STOP/trailing.

## Contractul (sursa unică de adevăr)

`providers/strategy_executor.py` — `Protocol StrategyExecutor` + `OrderStatus`,
`PairPrecision`, `ProviderError`. Harta față de KrakenClient:

| KrakenClient (azi) | Contract agnostic | Stare în providers/ |
|---|---|---|
| `add_order`→txid | `submit_order(...)->order_id` | ✅ Kraken/HL/Binance/T212 |
| `query_orders(txid)` | `order_status(symbol,id)->OrderStatus` | ✅ Kraken/HL/Binance/T212 |
| `cancel_order(txid)` | `cancel_order(symbol,id)` | ✅ Kraken/HL/Binance/T212 |
| `pair_info` | `pair_precision->PairPrecision` | ✅ Kraken/HL/Binance/T212 |
| `balance` | `free_balance(asset)` | ✅ Kraken/HL/Binance/T212 |
| `ohlc_closes` | `ohlc_closes(symbol,interval)` | ✅ Kraken/HL/Binance/T212 |
| ticker/quote | `get_current_price(symbol)` | ✅ Kraken/HL/Binance/T212 |

Dimensiune reală în `strategy.py`: **8 call-site-uri** (`self.client.*`) + **6** `except
KrakenError`. Backtestul (`replay.py`) folosește `MagicMock` → aproape neatins.

## Faze (fiecare cu gate)

- **Faza 0 — Contract + plasă de regresie** ✅
  - `providers/strategy_executor.py` (contractul: 7 metode + OrderStatus/PairPrecision/ProviderError).
  - `tests/test_kraken_strategy_golden.py` — GOLDEN: urma exactă (14 ordine, hash `69fd0a50…`) +
    metrici base v2 pe HYPE. **Trece neschimbat după tot refactorul.**
- **Faza 1 — Kraken pe contract** ✅ — `kraken_provider` delegă la `kraken_client` +
  `KrakenError→ProviderError`. 12 teste conformitate.
- **Faza 2 — Rewire `strategy.py`** ✅ · **golden BYTE-IDENTICAL** — 8 call-site → contract;
  6 `except`→`ProviderError`; `kraken_bot` injectează `KrakenProvider`; `get_current_price`
  adăugat în contract (gap `run()` prins). Test nou de căi live.
- **Faza 3 — Hyperliquid** ✅ — `submit_order`(gated `HL_LIVE_ORDERS`)/`order_status`(fills)/
  `cancel_order`/`pair_precision`/`ohlc_closes` reale. 11 teste conformitate.
- **Faza 4 — Binance** ✅ — `submit_order`/`order_status`(get_order)/`cancel_order`/
  `pair_precision`(filters)/`ohlc_closes`(klines). 7 teste. NB: completitudine — Binance base v2
  se suprapune cu tradeall; `order_status.fee=0` (aprox, refinabil din get_my_trades).
- **Faza 5 — Consolidare** ✅ — `tests/test_provider_contract_conformance.py`: gardă unică
  parametrizată (Kraken/HL/Binance/T212 satisfac `StrategyExecutor`).
- **Faza 5b — Motor în namespace neutru** ✅ — implementarea este în
  `strategies/spot_dca.py`; `kraken/strategy.py` este numai shim compatibil. Directorul
  de stare, notificatorul, sursa și eticheta venue-ului sunt injectabile. Replay-ul și
  testele importă modulul canonic, fără coliziuni între fișierele `strategy.py` ale venue-urilor.
- **Faza 5c — fidelitate + audit** ✅ — T212 reconciliază prețurile cumulative reale,
  inclusiv partial fills; `AuditedStrategyExecutor` adaugă `intent_id` și jurnal JSONL
  pentru submit/status/cancel fără să blocheze ordinele. Motorul autonom T212 folosește
  acum același contract pentru întreg ciclul ordinului; STOP/trailing sunt MARKET și
  replay-ul le modelează la open cu spread/slippage.
- **Faza 6 (amânată; necesită redesign)** — NU rutează direct base v2 prin
  `MarketApi.place`. Dacă apare nevoie cross-strategy, se introduce separat un decorator
  intent-aware, în care STOP/trailing nu pot fi blocate de trend/cooldown/plafon.

## Stare de închidere

Fazele 0–5c sunt integrate în `main` la `f5ac673`, validate cu golden-ul
byte-identical, benchmarkul financiar reproductibil și suita completă. Nu mai
există un gol de contract pentru providerii Kraken, Hyperliquid, Binance sau
Trading212. Faza 6 rămâne intenționat amânată și nu blochează închiderea
refactorului provider-agnostic.

## Invariant de siguranță
Gate-ul din Faza 2 = `tests/test_kraken_strategy_golden.py` trebuie să treacă **byte-identical**.
Dacă pică, refactorul a schimbat deciziile base v2 live → oprește și investighează.

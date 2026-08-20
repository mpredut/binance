# Unificare provider-agnostic a motorului de strategie (Calea B)

**Scop:** `kraken/strategy.py` (base v2: DCA+TP+trailing, validat live pe HYPE) rulează azi
DOAR pe Kraken — e cuplat de `KrakenClient` prin 6 metode și ocolește stratul agnostic
`MarketApi`. Calea B îl **unifică pe `MarketDataProvider`** ca să ruleze pe orice venue
(Kraken/Hyperliquid/Binance/Replay), cu o singură suită de teste de conformitate.

**De ce B (nu adaptor):** mai puțin cod (nu apare o a 3-a abstracție), mai testabil (o
suită parametrizată peste toți providerii), și base v2 poate intra pe `MarketApi.place`
cu guardrail-urile agnostice (cooldown/plafon/trend-wait/jurnal) pe care azi nu le are.

## Contractul (sursa unică de adevăr)

`providers/strategy_executor.py` — `Protocol StrategyExecutor` + `OrderStatus`,
`PairPrecision`, `ProviderError`. Harta față de KrakenClient:

| KrakenClient (azi) | Contract agnostic | Stare în providers/ |
|---|---|---|
| `add_order`→txid | `place_order(...)->order_id` | ✅ există (toți) — de garantat order_id + market |
| `query_orders(txid)` | `order_status(symbol,id)->OrderStatus` | ❌ **lipsește peste tot** |
| `cancel_order(txid)` | `cancel_order(symbol,id)` | ❌ **lipsește din interfață** (kraken_client + bapi o au intern) |
| `pair_info` | `pair_precision->PairPrecision` | ⚠️ parțial (doar `min_order_qty`) |
| `balance` | `free_balance(asset)` | ✅ există (toți) |
| `ohlc_closes` | `ohlc_closes(symbol,interval)` | ⚠️ mapabil pe `get_price_history` |

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
  parametrizată (kraken/HL/binance toți satisfac `StrategyExecutor`).
- **Faza 6 (opțional, separat)** — rutează base v2 prin `MarketApi.place` (guardrail-uri).
  Schimbare de comportament → re-validare dedicată, NU în gate-ul de regresie.

**Prima valoare** (Kraken neschimbat + HL activabil) după Faza 3 ≈ **3–4 zile**.
Golul real = `order_status` + `cancel` per venue.

## Invariant de siguranță
Gate-ul din Faza 2 = `tests/test_kraken_strategy_golden.py` trebuie să treacă **byte-identical**.
Dacă pică, refactorul a schimbat deciziile base v2 live → oprește și investighează.

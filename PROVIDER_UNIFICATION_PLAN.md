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

- **Faza 0 — Contract + plasă de regresie** ✅ *(acest commit)*
  - `providers/strategy_executor.py` (contractul).
  - `tests/test_kraken_strategy_golden.py` — GOLDEN: urma exactă de decizii (14 ordine,
    hash `69fd0a50…`) + metrici base v2 pe HYPE. **Trebuie să treacă neschimbat după refactor.**
- **Faza 1 — Kraken pe contract** *(~1 zi)*: implementezi cele 4 metode lipsă în
  `kraken_provider` prin **delegare la `kraken_client`** (le are deja) + `KrakenError→ProviderError`.
- **Faza 2 — Rewire `strategy.py`** *(~1 zi)* · **gate: golden pass + re-validare live Kraken**:
  8 call-site → contract; 6 `except`→`ProviderError`; `kraken_bot._build_client`→provider;
  `replay.py` o linie MagicMock. Kraken folosește `place_order` RAW (comportament identic).
- **Faza 3 — Hyperliquid** *(~1–2 zile)* · **gate: paper + shadow**: `order_status`+`cancel_order`
  reale (API HL le suportă) + `pair_precision`.
- **Faza 4 — Binance** *(~1–2 zile)*: `order_status`, `cancel_order` (din `bapi`), precizie.
- **Faza 5 — Consolidare** *(~0.5 zi)*: suită de conformitate parametrizată peste providerii.
- **Faza 6 (opțional, separat)** — rutează base v2 prin `MarketApi.place` (guardrail-uri).
  Schimbare de comportament → re-validare dedicată, NU în gate-ul de regresie.

**Prima valoare** (Kraken neschimbat + HL activabil) după Faza 3 ≈ **3–4 zile**.
Golul real = `order_status` + `cancel` per venue.

## Invariant de siguranță
Gate-ul din Faza 2 = `tests/test_kraken_strategy_golden.py` trebuie să treacă **byte-identical**.
Dacă pică, refactorul a schimbat deciziile base v2 live → oprește și investighează.

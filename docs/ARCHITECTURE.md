# ARCHITECTURE — decuplare & provideri (note de referință)

Snapshot de design (mijloc 2026). Verifică specificul în cod.

## Facadă market/cont — decuplare de Binance
`providers/market_api.py` = facadă care rutează pe **symbol** către provideri (scopul: trade-
monitorul devine generic, nu doar Binance).
- Interfața `MarketDataProvider`: `get_current_price`, `get_price_history`, `free_balance(asset)`,
  `get_orders(symbol, side, since)`, `get_trades`, `open_orders`,
  `place_order(symbol, side, price, qty, **kwargs)`.
- Provideri: `BinanceProvider`, `HyperliquidProvider`, `kraken_provider`, `t212_provider`.
- `MarketApi([providers])` alege primul provider cu `supports_symbol(symbol)`; dacă niciunul
  nu revendică → **default = primul = Binance** (behavior-preserving). Singleton `api`.
- `monitortrades` folosește facada pentru preț, trend, sold, ordine + `place_order`. Binance
  rămâne identic (BinanceProvider deleagă la `bapi`/`bapi_placeorder`).
- Instrument generic + `instruments.conf` (rezolvare `provider_by_name`); BTC/TAO pe Binance neschimbate.

### HYPE pe Hyperliquid (SPOT)
`providers/hyperliquid_provider.py`:
- preț/history **public** HL (perechea @index, ex `@107` = HYPE/USDC);
- `free_balance` = SPOT (`total − hold`); `get_orders`/`get_trades` = fill-uri SPOT
  (`coin == @index`; fill-urile PERP `coin=HYPE` sunt EXCLUSE → DN-ul nu se amestecă);
- refolosește `hyperliquid/hl_client.py` (SDK), cu **import LAZY** — fleet-ul NU pică dacă
  SDK-ul HL lipsește din venv-ul lui (Binance neafectat).
- **Porți (default OFF):** `MT_HYPE_ENABLED` (HYPE în bucla `monitortrades`), `HL_LIVE_ORDERS`
  (ordine reale; altfel doar `[HL][DRY]`).
- ⚠ **Co-mingling spot DN** (vezi [OPERATIONS.md](OPERATIONS.md) §3): soldul spot HYPE e UNUL
  pe wallet, partajat cu piciorul DN → de-aia ordinele reale HYPE stau OFF până la separare.

## Kraken multi-proces (cacheManager replicat)
Pentru 2–3 procese de trading HYPE pe Kraken (același symbol `HYPEUSD`), pe UN singur cont:
- **`kraken/kraken_cachemanager.py`** = proces SEPARAT (izolare de Binance: Kraken jos ≠ Binance jos)
  care ține fill-urile într-un cache cu NAMESPACE separat (`cachedb/cache_trade_kraken.json`);
  `kraken_provider.get_orders` CITEȘTE din el (gard de profit corect cross-proces + un singur
  feed = rate-limit ok), cu fallback pe `TradesHistory`.
  - mod **poll** (default, ~5s) / mod **ws** (`KRAKEN_CACHE_MODE=ws`, `ownTrades` real-time —
    cod gata dar neactiv; pt scalping sub 5s, cere `websocket-client`).
- **Nonce Kraken e per-CHEIE** strict crescător → fiecare proces are PERECHEA lui de chei
  (`KRAKEN_API_KEY` / `_WS`), altfel „Invalid nonce". Cheile DOAR în `kraken/.env*`.
- **Balanță:** un cont → toate procesele văd același `free_balance` (risc over-sell pe același
  symbol); mitigat de weight-cap + cooldown + respingerea bursei. Extra (doar la nevoie):
  strat de rezervare de balanță în cache-ul comun.

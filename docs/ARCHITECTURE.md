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

## Trailing stop (core partajat + adaptoare per provider)
Disjunctor de CRASH pe holdingurile manuale (NU alfa): prag LARG (Binance 20–22%, Kraken 15%)
se declanșează doar la colaps susținut. Refactor 2026-06: logica era duplicată ~linie-cu-linie
în cele 2 `trailing_stop.py` → mutată în `trailing_core.TrailingCore` (scrisă o singură dată).
- **`trailing_core.py`** = mașina de stări (provider-agnostic): warmup → urmărește vârful →
  vinde la −trail% → re-buy pe recul de la minim. **`binance_api/trailing_stop.py`** +
  **`kraken/trailing_stop.py`** = ADAPTOARE subțiri (clasele `TrailingStop`/`KrakenTrailing`),
  doar API-ul lor + log/notify. Rămân **2 fișiere = 2 procese**/config/stări separate (dedup ≠ 1 fișier).
- **Contract adaptor** (duck-typing): `assets()→(key,asset,pair,trail)`, `begin_tick()→bool`,
  `free_qty(asset)`, `price(pair)`, `trend(pair)`, `execute_sell(...)→bool`, `execute_rebuy(...)→bool`,
  + `log_*` (wording specific). Provider nou = doar aceste metode; logica de decizie NU se rescrie.
- **Mașina de stări** (`_process`, per activ/tick): (1) **warmup** dacă `min_profit_pct>0` (nu
  armează până `price≥entry·(1+min%)` — evită sell în pierdere după un dip imediat ce-ai cumpărat);
  (2) **re-buy** pending (recul `+bounce%` de la minim, sări dacă trend clar jos); (3) sub notional →
  sări; (4) `price>peak` → urcă vârful; (5) `price≤peak·(1−trail%)` → vinde `free·sell_fraction`,
  re-armează vârf + armează `rebuy`.
- **Stare persistată** (schemă neschimbată de refactor): `{"<key>": {"peak", "rebuy":{qty,sell_price,low}?, "warmup_at"?}}`.
  Binance `cachedb/trailing_state.json` (cheie=symbol), Kraken `kraken/trailing_state.json` (cheie=asset).
  Supraviețuiește restartului (vârful nu se resetează).
- **`item_isolation`** (model de erori, diferă real): Binance `True` = try per-monedă + save mereu;
  Kraken `False` = try pe tot tick-ul, fără save la eroare.
- **Config**: `*/trailing.conf` — `(KRAKEN_)TRAILING_ENABLED`=LIVE (default dry-run), `_REBUY_*`,
  `_MIN_PROFIT_PCT`; praguri/`CHECK_SECONDS` în cod (Binance 60s, Kraken 120s).
  **Notify**: Kraken cheamă `notify()` (ntfy+email, `source=kraken-trail`) la sell/rebuy; **Binance NU
  notifică** (doar log `trail_b.log`, care e block-buffered → confirmă via state file / `--status`).
- **Teste** (garantează echivalența refactorului): `tests/test_trailing_stop.py`,
  `kraken/test_trailing_kraken.py`. CLI: `--once`, `--status`. Lansare din `bots_start.sh`,
  supravegheat de `healthcheck.sh --supervise` (vezi [OPERATIONS.md](OPERATIONS.md)).

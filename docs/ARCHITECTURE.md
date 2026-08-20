# ARCHITECTURE — decuplare & provideri (note de referință)

Snapshot de design (mijloc 2026). Verifică specificul în cod.

## Helper-e runtime comune

`botcore.py` este sursa unică pentru `.env`, conversii numerice, single-instance,
ceas/log și transportul HTTP stdlib (`GET`, JSON, form și metode generice).
`kraken/kraken_common.py`, `hyperliquid/common.py` și
`212trading/ipo_common.py` păstrează numai particularități reale de afișare sau
runtime și re-exportă API-ul vechi pentru compatibilitate.

`alertnotifiers.bind_notify()` centralizează alegerea simbolului din environment.
Fișierele `notify.py` ale venue-urilor sunt shim-uri subțiri, necesare momentan
pentru entrypoint-urile istorice; rutarea ntfy/email rămâne o singură implementare.
Aceeași componentă aplică deduplicare și bugete zilnice persistente cross-process
(implicit ntfy 100 cu 20 rezervate urgențelor; email 40 cu 10 rezervate). Starea
runtime este în `logs/notification_delivery_state.json` și se resetează zilnic UTC.

## Engine comun, entrypoint-uri separate

Separarea entrypoint-ului live de cel offline nu înseamnă două strategii:

```text
                         strategy engine comun
                        /                      \
live entrypoint ─► StrategyExecutor real   replay entrypoint ─► executor OHLC
  config/secrete     ordine/reconciliere      dataset/hash       fill model/report
  loop/heartbeat     state persistent         fără rețea privată  stare controlată
```

Motorul, regulile, parametrii și tranzițiile financiare trebuie importate din
același modul. Se separă numai orchestration-ul și capabilitățile: procesul offline
nu primește client cu drept de tranzacționare, iar procesul live nu conține selecție
de dataset sau metrici de cercetare. Un renderer comun poate fi folosit de două
entrypoint-uri subțiri live/offline fără duplicarea logicii.

## Ownership inventory și execution audit

Sistemul nu folosește allocation ledger cât timp conturile sunt izolate și
suprapunerile de execuție sunt rare. Două unelte read-only acoperă nevoia actuală:

- execution audit: cine a cerut ordinul, pe ce venue/simbol, de ce, ce status/fill a avut;
- `verify_tools/ownership_inventory.py`: ce owner poate executa pe fiecare
  `venue + account_ref + symbol` și unde există suprapuneri configurate/rulate.

Inventarul nu citește și nu afișează chei, nu blochează ordine și nu schimbă live.
Un `account_ref` explicit, nesensibil, poate fi setat per owner sau prin
`ownership.account_ref` pe un instrument; fallback-ul este `<venue>:default`.
Două strategii primary din același pipeline coordonat sunt doar `INFO`; două
domenii de execuție independente pe aceeași cheie sunt `WARNING`.

```bash
.venv/bin/python verify_tools/ownership_inventory.py
.venv/bin/python verify_tools/ownership_inventory.py --running
.venv/bin/python verify_tools/ownership_inventory.py --running --json
```

Ledger-ul se reconsideră numai dacă devine intenționată tranzacționarea frecventă
din mai multe procese independente pe aceeași balanță.

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

## Motor spot DCA/trailing

`strategies/spot_dca.py` conține decizia financiară base v2 și depinde numai de
`StrategyExecutor`. Kraken injectează `KrakenProvider`, iar replay-ul injectează
executorul offline; ambele rulează aceeași clasă. `kraken/strategy.py` rămâne shim
pentru comenzile istorice. Directorul stării, notificatorul și eticheta venue-ului
sunt injectabile, dar fallback-ul Kraken păstrează exact fișierul de stare existent.

T212 și Hyperliquid nu sunt alias-uri ale acestui motor: providerii lor satisfac
contractul mecanic, însă strategiile financiare distincte rămân separate.

`strategies/state_store.py` centralizează snapshot-urile financiare pentru motorul
spot și T212. Scrierea este atomică (`fsync` urmat de `os.replace`); în mod real,
starea coruptă sau nesalvabilă oprește deciziile, în timp ce PAPER poate porni curat.

Motorul T212 păstrează local un ordin până când venue-ul raportează status terminal,
inclusiv după acceptarea cererii de anulare. Dacă anularea eșuează sau este încă în curs,
nu plasează repricing/scară TP peste ordinul posibil activ; STOP/trailing poate trimite
ieșirea urgentă după acceptarea anulărilor, dar ambele ordine rămân reconciliate.

Motorul T212 folosește `T212Provider` pentru întreg ciclul submit/status/cancel, dar își
păstrează regulile financiare distincte și feed-ul Yahoo. Cantitatea poziției rămâne
ancorată în portofoliu, iar prețul/P&L-ul se ia din fill-urile cumulative reale numai
când delta ordinelor corespunde deltei portofoliului. Partial fill-urile sunt aplicate
o singură dată; dacă statusul este temporar indisponibil, ordinul rămâne urmărit.
STOP și trailing sunt ordine MARKET; replay-ul le umple la open-ul barei următoare și
poate aplica spread/slippage advers.

`providers/execution_audit.py` este un decorator strict observațional peste
`StrategyExecutor`. Fiecare intenție live primește `intent_id`, păstrat în starea
ordinului, iar submit/status/cancel sunt scrise JSONL în `logger/execution_audit/`.
Eșecul auditului nu poate refuza și nu poate modifica un ordin.

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

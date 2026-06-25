# STRATEGY — logica de trading & decizii (note de referință)

Snapshot al intenției de design (mijloc 2026). Pentru praguri/commits exacte verifică
codul curent — aici sunt „de ce"-urile durabile, nu starea live.

## Detecția de trend (`priceAnalysis.py` + `trend_survival.py`)
- **Lag de detecție +48h INTENȚIONAT.** Durata trendului include un lag de ~2 zile: empiric,
  când detectorul confirmă un trend, el începuse cu ~2 zile înainte. Parametru explicit
  `detection_lag_hours` (default 48 în `getTrendLongTerm_fixed`, 0 în funcția pură
  `detect_long_term_trend`), plafonat la span-ul datelor. **NU-l elimina la refactor** (a fost
  „reparat" greșit o dată înainte de a se explica intenția).
- **Ponderea de cash (`get_trade_weight`) = curbă de supraviețuire empirică, nu gaussiană fixă T=14.**
  - `trend_survival.py`: S(vârstă) per monedă; `estimate_T(symbol)` (T_emp = max(P90, 2·mediană),
    hibrid cu prior 14, cache disc TTL 7z). Live BTC/TAO → T≈8 zile.
  - **`lindy_plateau=True`**: P(continuare|vârstă) e ~plată (0.65–0.75) la toate vârstele →
    după vârf ponderea rămâne la vârf („pe final de trend poartă-te ca la mijloc" — validat empiric).
  - **Filtru Mann-Kendall** (`mk_alpha=0.05`) taie ~30% din ferestre ca zgomot; Hurst informativ.
  - **Regimul de piață NU schimbă durata** — invariantă la bull/bear/range (mediană ~3z identică);
    analizat și abandonat (per-regim = artefact de eșantion mic). T global + plateau e suficient.
  - `forecast.py` = modul paralel (test, NU tranzacționează); nu bate baseline-ul lindy.
    LSTM (`priceprediction.py`, Keras) nu rulează — tensorflow nu e în venv.

## Garda de profit Binance — fereastră 12 zile
`monitortrades` + `bapi_placeorder.if_place_safe_order`: garda ia referința din ultimele
`MT_GUARD_WINDOW_DAYS` (default **12**) zile (`min(sell)` pt BUY / `max(buy)` pt SELL). Era 14 —
un sell vechi bloca re-intrarea după un crash (incident TAO iun 2026); redus la 12.

## Trailing — disjunctor de crash + re-buy
`binance_api/trailing_stop.py` + `kraken/trailing_stop.py` (config în `*/trailing.conf`):
- **Disjunctor:** vinde balanța LIBERĂ dacă prețul cade de la vârf (BTC ~−22% / −20% / Kraken −15%),
  `force=True`, NU atinge pozițiile blocate în ordinele TP. Recomandat NECONDIȚIONAT de trend
  (să nu blochezi protecția pe un trend citit greșit).
- **Re-buy după crash** (`TRAILING_REBUY_ENABLED`): după un stop de crash armează re-buy în
  `cachedb/trailing_state.json`; recumpără când prețul revine `TRAILING_REBUY_BOUNCE_PCT`%
  (~1.2) de la minim, cu `bypass_profit_guard`. Sare dacă trendul e CLAR jos. `min_profit_pct`
  înainte de activare (să nu vândă în pierdere pe un dip normal).

## T212 (`212trading/t212_bot.py` — un proces, thread per `config.*.env`)
- **Profit-guard** (SPCX, NVDA): vinde DOAR pe profit (TP), `STRAT_STOP_LOSS_PCT=30` = doar
  catastrofă; cumpără DOAR sub ultima vânzare (re-entry guard). Nu vinde în pierdere normală
  (riscul asumat: capital blocat pe scădere, exit la pierdere doar la −30%).
- **Scale-out TP ladder** (`STRAT_TP_LADDER`, ex `11:33,20:33,30:34`): vinde în trepte la +11/+20/+30%.
- **Config generic** (`MAX_BUDGET` + `STRAT_ENTRY_PCT`/`STRAT_DCA_PCT`, `MAX_DCA_BUYS=auto`):
  schimbi bugetul → entry/DCA/contor scalează singure.
- **FX:** cont T212 cu bază RON → ordinele rămân `currency=RON`, FX `STRAT_FX_FEE_PCT` (0.15%)/direcție
  se aplică. Convertirea cashului NU schimbă baza contului — doar un cont NOU cu bază USD scapă de FX.
- **Lecții:** (1) la `selling-not-owned` verifică ordinele PENDING (rezervă), nu doar starea;
  (2) ladder: ultima transă = held − suma celorlalte ȘI lasă ~$5–6 liber (`STRAT_LADDER_MIN_FREE`),
  altfel T212 respinge (`min-opened-position`); (3) alerte O DATĂ per episod / high-water-mark
  (spam-ul de stop-loss a umplut cota gratuită ntfy.sh → 429).

## Kraken xStocks (SPCX etc.) = DOAR watcher
`kraken/kraken_xstock_watch.py` NU tranzacționează prin API (xStocks nu apar pe `asset_pairs`);
doar monitorizează balanță + nivele → **ALERTE** (nu vânzări). SpaceX se tranzacționează prin
**T212** (real). Vezi și [ARCHITECTURE.md](ARCHITECTURE.md).

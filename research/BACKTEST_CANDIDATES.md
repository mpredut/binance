# Candidati pentru backtest/tuning — inventar centralizat (23 iul 2026)

Lista tuturor constantelor/multiplicatorilor/pragurilor din boti care merita
un backtest dedicat, cu un grid de valori de testat (≤5 valori/variabila).
Sursa: extragerile in `*_config.env` din aceasta sesiune + investigatiile deja
rulate (`research/kraken_adaptive_thresholds/`, `research/tradeall_trigger_gate/`,
`research/tradeall_adaptive_thresholds/`, `research/tradeall_kalman_lag/`).

Legenda status: 🔴 netestat inca | 🟡 partial testat (alt aspect, nu valoarea
insasi) | 🟢 deja testat riguros (rezultat cunoscut, listat) | ⏳ sweep in curs azi.

---

## Prioritate ÎNALTĂ

| # | Fisier / bot | Variabila | Valoare azi | Status | Grid propus (pas) |
|---|---|---|---|---|---|
| 1 | `tradeall_config.env` | `TRADEALL_PRICE_CHANGE_THRESHOLD_PCT` (SMALL) | 0.518% fix | 🟢 **RAMANE FIX** | Testat 23-24 iul, K∈{0.1,0.2,0.3,0.5}: TOATE catastrofal mai rele (BTC net -$29k..-$38k, TAO -$9k..-$119k, fata de FIX: BTC -$4.9k, TAO +$1.4k). Overtrading masiv (BTC k0.1: 6434 buy-uri vs 186 la fix). Concluzie decisiva, nu marginala — NU promova. |
| 2 | `tradeall_config.env` | `TRADEALL_PRICE_CHANGE_THRESHOLD_BIG_PCT` | 2.481% fix | 🟢 **RAMANE FIX** | Cuplat cu #1 (raport fix ~4.79×), acelasi verdict. |
| 3 | `shadow_signals.py` | `SHADOW_KALMAN_SAMPLE_SEC` | 60s | 🟢 **RAMANE 60s** | Testat 23-24 iul, {20,60,90,150}s: 20s → 18696 tranzitii/zgomot, overtrading catastrofal (net -$9k/-$10k); 90s/150s → ZERO tranzitii Kalman in tot istoricul (filtrul devine prea incert sa mai confirme vreun trend); 60s (actual) → doar 18 tranzitii, net usor POZITIV ($15.34 BTC). 60s e deja optim intre "prea zgomotos" si "complet surd", nu doar o valoare arbitrara. |
| 4 | `instruments.conf` `[BINANCE_BTC]` | `mt.gain` / `mt.lost` | 7.0% / 3.3% | 🔴 | gain: {5, 6, 7, 8, 9}% · lost: {2.3, 2.8, 3.3, 3.8, 4.3}% |
| 5 | `instruments.conf` `[BINANCE_TAO]` | `mt.gain` / `mt.lost` | 9.2% / 4.9% | 🔴 | gain: {7, 8, 9.2, 10.5, 12}% · lost: {3.5, 4.2, 4.9, 5.6, 6.3}% |
| 6 | `kraken/config.env` | `STRAT_DCA_DROP_PCT` (valoarea FIXĂ insasi, nu K-ul adaptiv) | 1.0% | 🟡 (doar adaptiv-vs-fix testat, K=1.0→fix a fost CEL MAI SLAB K adaptiv) | {0.5, 0.75, 1.0, 1.5, 2.0}% |
| 7 | `kraken/config.env` | `STRAT_TAKEPROFIT_PCT` | 5.0% | 🟡 (comentariu vechi "sweep +8.8%" — verifica daca inca valabil) | {3.5, 4.25, 5.0, 6.0, 7.5}% |
| 8 | `tradeall_config.env` | `TRADEALL_FIRE_MIN_RETRY_MINUTES` | 6 min | 🟡 (o singura config. testata: 6 min a batut 30 min) | {3, 4.5, 6, 9, 12} min |
| 9 | `tradeall_config.env` | `TRADEALL_FIRE_MAX_PER_TREND` | 3 | 🟡 (ales direct de user, netestat prin sweep) | {1, 2, 3, 4, 5} |

---

## Prioritate MEDIE

| # | Fisier / bot | Variabila | Valoare azi | Status | Grid propus (pas) |
|---|---|---|---|---|---|
| 10 | `kraken/config.env` | `STRAT_ORDER_TTL_MIN` | 10 min | 🔴 | {5, 7.5, 10, 15, 20} min |
| 11 | `kraken/config.env` | `STRAT_STOP_LOSS_PCT` | 7% | 🔴 | {5, 6, 7, 9, 11}% |
| 12 | `kraken/config.env` | `STRAT_ENTRY_DISCOUNT_PCT` | 0.8% | 🔴 | {0.3, 0.55, 0.8, 1.2, 1.6}% |
| 13 | `monitortrades_config.env` | `MT_SELL_SAFEBACK_HOURS` | 2h | 🔴 | {1, 1.5, 2, 3, 4}h |
| 14 | `monitortrades_config.env` | `MT_BUY_SAFEBACK_HOURS` | 48h | 🔴 | {24, 36, 48, 60, 72}h |
| 15 | `monitortrades.conf` (global fallback) | `hard_tp_pct` / `hard_tp_fraction` | 17% / 0.5 | 🔴 | pct: {12, 14.5, 17, 20, 24}% · fractie: {0.25, 0.4, 0.5, 0.65, 0.8} |
| 16 | `instruments.conf` `[BINANCE_BTC/TAO]` | `mt.maxage_days` | 7 / 17 | 🔴 | BTC: {4, 5.5, 7, 10, 14} · TAO: {10, 13, 17, 22, 28} |
| 17 | `assetguardian_config.env` | `AG_TARGET_DROP_PCT` | 7% | 🔴 | {4, 5.5, 7, 9, 12}% |
| 18 | `assetguardian_config.env` | `AG_REFERENCE_MINUTES_BACK` | 1440 min (24h) | 🔴 | {360, 720, 1440, 2160, 2880} min (6h→48h) |
| 19 | `rtrade_config.env` | `RTRADE_BAD_DAY_MULTIPLIER` | 1.7 | 🔴 | {1.2, 1.45, 1.7, 2.1, 2.5} |
| 20 | `rtrade_config.env` | `RTRADE_BUY_NORMAL_HOURS` / `RTRADE_SELL_NORMAL_HOURS` | 16h / 12h | 🔴 | BUY: {8,12,16,20,24}h · SELL: {6,9,12,15,18}h (pastreaza asimetria) |
| 21 | `rtrade_config.env` | `RTRADE_BUY_DECAY_PCT` / `RTRADE_SELL_DECAY_PCT` | 0.005 / 0.01 | 🔴 | BUY: {0.002,0.0035,0.005,0.008,0.012} · SELL: {0.004,0.007,0.01,0.015,0.02} |
| 22 | `shadow_signals.py` | `SHADOW_KALMAN_QR` | 0.0005 | 🟡 (sweep 17 iul mentionat in comentariu, nu regasit ca script salvat) | {0.0002, 0.00035, 0.0005, 0.001, 0.002} |
| 23 | `shadow_signals.py` | `SHADOW_KALMAN_EXIT` (CONF_EXIT, histerezis) | 0.8 | 🔴 | {0.5, 0.65, 0.8, 1.0, 1.2} |

---

## Prioritate SCĂZUTĂ (infra/robustete, impact P&L probabil mic — de luat in calcul doar dupa cele de mai sus)

| # | Fisier / bot | Variabila | Valoare azi | Status | Grid propus (pas) |
|---|---|---|---|---|---|
| 24 | `tradeall_config.env` | `TRADEALL_TREND_UNIFORM_RATE` | 0.08 | 🔴 | {0.04, 0.06, 0.08, 0.12, 0.16} |
| 25 | `tradeall_config.env` | `TRADEALL_SLOPE_EXTREME_THRESHOLD` | 5.1 | 🟡 (alte variante de "prag extrem" testate indirect, nu acest exact prag) | {3, 4, 5.1, 6.5, 8} |
| 26 | `monitortrades_config.env` | `MT_ARE_CLOSE_TOLERANCE_PCT` | 1.0% | 🔴 | {0.5, 0.75, 1.0, 1.5, 2.0}% |
| 27 | `monitortrades_config.env` | `MT_RECENT_TRADE_BLOCK_HOURS` / `MT_ALL_TRADES_BLOCK_HOURS` | 3h / 1h | 🔴 | 3h→{1.5,2.25,3,4,5}h · 1h→{0.5,0.75,1,1.5,2}h |
| 28 | `rtrade_config.env` | `RTRADE_FOLLOWUP_HOURS` | 2.7h | 🔴 | {1.5, 2.1, 2.7, 3.5, 4.5}h |
| 29 | `rtrade_config.env` | `RTRADE_MIN_ADJUSTMENT_PCT` | 0.01 | 🔴 | {0.005, 0.0075, 0.01, 0.015, 0.02} |
| 30 | `kraken/config.env` | `STRAT_REENTRY_TOLERANCE_PCT` | 0.05% | 🔴 | {0.02, 0.035, 0.05, 0.08, 0.12}% |

---

## Neincluse deliberat (nu merita backtest de P&L)

- **Intervale de polling** (`MT_MAIN_LOOP_SLEEP_SEC`, `AG_CHECK_INTERVAL_SEC`,
  `RTRADE_WAIT_FOR_ORDER_SEC`, `STRAT_CHECK_MINUTES`) — afecteaza latenta de
  reactie, nu logica de decizie; un backtest bazat pe tick-uri istorice nu le
  poate testa realist oricum (rezolutia arhivei e mai grosiera decat unele
  din aceste intervale).
- **Epsiloane numerice** (`RTRADE_ZERO_EPSILON`, tolerantele de reconciliere
  0.001/1.003 din `kraken/strategy.py`) — exista sa evite erori de precizie
  flotanta / respingeri false, nu sa optimizeze P&L.
- **Marimi de pozitie/buget** (`RTRADE_QTY`, `STRAT_ENTRY`, `STRAT_DCA`,
  `STRAT_MAX_BUDGET`, `AG_BUY_USE_CASH_RATIO`) — dimensionare de capital/risc,
  nu parametri de strategie; schimbarea lor scaleaza P&L-ul liniar fara sa
  schimbe CAND se tranzactioneaza, deci un "backtest de tuning" clasic (care
  cauta cel mai bun raport risc/profit) nu se aplica la fel de direct — decizia
  aici e mai degraba de alocare de capital decat de semnal.
- **`CONF_ENTER`, `MIN_VEL_PCT_MIN`, `GAP_RESET_SEC`** din `shadow_signals.py`
  — hardcodate, FARA mecanism de override prin env inca (spre deosebire de
  restul constantelor Kalman). Ar trebui intai extrase in `SHADOW_*` (ca
  `SHADOW_KALMAN_EXIT`) inainte sa poata fi backtestate prin sweep, la fel ca
  restul.
- **`AG_TARGET_GROWTH_PCT` (100%)** — intentionat "practic oprit" dupa un
  walk-forward anterior (291 zile) care a aratat ca vanzarea agresiva pe
  crestere pierde fata de detinere; re-testarea lui ar relua o concluzie deja
  stabilita, nu adauga informatie noua fara un motiv nou sa o pui la indoiala.

---

## Recomandare de ordine (actualizat 24 iul, dupa pilotul + sweep-urile de peste noapte)

1. ~~**#4-5** (gain/lost per simbol pe monitortrades)~~ — FACUT: pilotul
   (`research/monitortrades_backtest/scheduled_pilot.py`) a rulat toate 4,
   confirmat si aplicat TAO `mt.lost` 4.9→5.25 (singurul semnal confirmat pe
   ambele ferestre istorice), respins corect restul 3 ca zgomot. De asemenea
   gasit si reparat (nu era pe lista initiala): lipsa `mt.buy_budget`/
   `mt.max_budget` pt BTC/TAO (risc real — "buy again" cumpara qty=1 unitate
   INTREAGA fara ele).
2. ~~**#1-3** (praguri adaptive tradeall + Kalman sample rate)~~ — FACUT:
   ambele RAMAN FIXE, verdict decisiv (vezi tabelul de mai sus).
3. **#6-7** (kraken DCA/TP ca valori fixe, nu doar adaptiv-vs-fix) — inca netestat.
4. **#15-16** (hard-TP global + maxage per instrument, monitortrades) — inca netestat.
5. Restul, dupa ce acestea arata daca merita continuat efortul.

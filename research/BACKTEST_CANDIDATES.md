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
| 1 | `tradeall_config.env` | `TRADEALL_PRICE_CHANGE_THRESHOLD_PCT` (SMALL) | 0.518% fix | 🟢 **RAMANE FIX** | Testat 23-24 iul, K∈{0.1,0.2,0.3,0.5}: TOATE catastrofal mai rele (BTC net -$29k..-$38k, TAO -$9k..-$119k, fata de FIX: BTC -$4.9k, TAO +$1.4k). Overtrading masiv (BTC k0.1: 6434 buy-uri vs 186 la fix). Concluzie decisiva, nu marginala — NU promova. **Root-cause verificat 28-29 iul** (raspuns la observatia user "overtradingul eclipseaza castigul?"): cooldown-ul din tradeall.py (live din 22 iul, `_fire_once`) e PER INSTANTA DE TREND — se reseteaza la fiecare `start_trend()`, deci NU limiteaza frecventa cand pragul adaptiv e atat de sensibil incat porneste trenduri NOI la fiecare ~9 min (masurat: K=0.1, 12h -> 82 trend-starts DISTINCTE, 110 executii confirmate, media doar 1.34 fire-uri/trend — sub plafonul de 3, deci cooldown-ul nici nu apuca sa se activeze). Semnalul BRUT a fost chiar usor POZITIV (realized $86.53), dar comisioanele ($108.05) l-au depasit — confirma EXACT observatia: nu e bug de test, e overtrading real prin CHURN de trend-uri (nu prin refire pe unul persistent, pe care cooldown-ul deja il opreste). Verdictul RAMANE (proportional pe 336 zile), dar cu mecanism precis identificat, nu doar etichetat "catastrofal". **RE-CONFIRMAT 30 iul** (sweep complet fresh, date de azi): BTC FIX -5193 vs k0.1..0.5 -29k..-38k; TAO FIX +1480 vs -8k..-121k. FIX castiga masiv pe TOATE K, decisiv. |
| 2 | `tradeall_config.env` | `TRADEALL_PRICE_CHANGE_THRESHOLD_BIG_PCT` | 2.481% fix | 🟢 **RAMANE FIX** | Cuplat cu #1 (raport fix ~4.79×), acelasi verdict. |
| 3 | `shadow_signals.py` | `SHADOW_KALMAN_SAMPLE_SEC` | 60s | 🟢 **RAMANE 60s** | Testat 23-24 iul, {20,60,90,150}s: 20s → 18696 tranzitii/zgomot, overtrading catastrofal (net -$9k/-$10k); 90s/150s → ZERO tranzitii Kalman in tot istoricul (filtrul devine prea incert sa mai confirme vreun trend); 60s (actual) → doar 18 tranzitii, net usor POZITIV ($15.34 BTC). 60s e deja optim intre "prea zgomotos" si "complet surd", nu doar o valoare arbitrara. **RE-CONFIRMAT 30 iul** (kalman_lag sweep, date azi): 20s -9k/-10k (18-19k tranzitii); 60s +15.34 BTC; 90/150s 0 tranzitii. Identic — 60s ramane optim. |
| 4 | `instruments.conf` `[BINANCE_BTC]` | `mt.gain` / `mt.lost` | 6.0% / 3.55% (28 iul, aplicat de pilot) | 🟡 **RE-VALIDAT 30 iul cu semnal REAL: FARA castigator** | Sweep cu semnal REAL (ReplayTrendSource, ~337z): TOATE variantele pierd si sunt SUB buy&hold. Strangerea gain 7.0->6.25 + lost 3.3->3.49 INRAUTATESTE BTC (net -443 -> -813). Valorile aplicate (6.0/3.55) NU-s demonstrat mai bune ca cele vechi (7.0/3.3). Verdictul vechi (neutralizat) nu e rasturnat spre altceva clar — pur si simplu niciun set nu bate buy&hold pe piata in declin (BTC 111k->64k). Concluzie: nicio schimbare justificata; parametrul are pargie mica cu semnal real. |
| 5 | `instruments.conf` `[BINANCE_TAO]` | `mt.gain` / `mt.lost` | 9.2% / 5.25% (aplicat 23-24 iul) | 🟡 **RE-VALIDAT 30 iul cu semnal REAL: FARA castigator** | Sweep cu semnal REAL: TAO net -1177..-1521, toate SUB buy&hold (-434). `mt.lost` 4.9->5.25 nu produce un castig clar cu semnal real (oscileaza, nu monoton). Ca #4: niciun set nu bate buy&hold pe TAO in declin (336->190). Valorile aplicate raman, dar nu-s demonstrat superioare — pargie mica. |
| 6 | `kraken/config.env` | `STRAT_DCA_DROP_PCT` | 1.0% → **1.25%** (28 iul) | 🟢 **APLICAT 1.0→1.25** | Sweep #6 pe HYPEUSD (kraken/backtest.py), 2 regimuri: 1.5 bate 1.0 pe AMBELE — bull 120z (+6.68% vs +5.85%, maxDD $156 vs $186) + decline 30z (-0.40% vs -0.55%, maxDD $202 vs $220), return SI drawdown mai bune. Semnal MODEST pe date HYPE-only care se SUPRAPUN (30z = coada celor 120z), deci amortizat la media 1.25 (ca TAO mt.lost). Caveat: Kraken API da doar ~720 bare recente — fara ferestre vechi INDEPENDENTE. |
| 7 | `kraken/config.env` | `STRAT_TAKEPROFIT_PCT` | 5.0% | 🟢 **RAMANE 5.0** | Sweep #7, 2 regimuri: tp=5.0 (actual) e CEL MAI BUN pe AMBELE, decisiv — bull 120z (+5.85% vs +2.40% urmatorul) + decline 30z (-0.55% vs -2.01% urmatorul). Confirma nota veche "sweep +8.8%". Nicio schimbare. |
| 8 | `tradeall_config.env` | `TRADEALL_FIRE_MIN_RETRY_MINUTES` | 6 min | 🟡 **PARGIE MINIMA, current OK** (re-rulat 30 iul, fereastra tintita) | Re-rulat pe arhiva densa 14 iul->30 iul (~15z, `experiment_fire_params_sweep.py`) care CHIAR contine trend-starts. BTC: 0 starts (nu se declanseaza pe fereastra). TAO: 3 starts / 116 confirms, dar cooldown blocheaza 184 -> doar 1-2 fires confirmate. retry {3,4.5,6,9,12}min: net -14..-34 (thin, 1-2 trades). Chiar CU evenimente reale, cooldown+fire-limit domina -> parametrul are pargie minima. 6min ramane fine, niciun castigator. |
| 9 | `tradeall_config.env` | `TRADEALL_FIRE_MAX_PER_TREND` | 3 | 🟡 **PARGIE MINIMA, current OK** (re-rulat 30 iul) | Sweep max {1,2,3,4,5} pe aceeasi fereastra tintita: TAO -27..-36, similar intre valori (doar 1-2 fires oricum). 3 ramane fine. Vezi #8. |

### ⚠ GASIRE 30 iul — `STRAT_REENTRY_ADAPTIVE` (Kraken/HYPE, LIVE azi) posibil de DEZACTIVAT

`kraken/config.env STRAT_REENTRY_ADAPTIVE=true` (prag de reintrare adaptiv = K_REENTRY*vol,
promovat la decizie reala). Re-rulare 30 iul (`verify_adaptive_reentry.py`/`sweep_k_multiplier.py`,
date proaspete): verdict **INVERSAT** fata de README-ul vechi — FIX (2.2%) bate acum adaptivul
(K=2.0, setarea LIVE) pe TOATE criteriile (net -2.09% vs -3.51%, win-rate, maxDD). Fereastra
Kraken s-a mutat spre declin (buy&hold -18.4%), regim in care adaptivul pierde. **CAVEAT: eSantion
mic** (Kraken API ~720 bare, 3-5 cicluri, fereastra care se misca) -> dependent de regim, NU inca
solid. De verificat robust (multiple ferestre) inainte de a dezactiva. Singurul semnal ACTIONABIL
din toata suita — restul confirma valorile fixe/actuale.

**✅ APLICAT + RE-CONFIRMAT (30-31 iul):** verificat ROBUST (14 ferestre: 4h ~120z + 1h ~30z,
`scratchpad/reentry_robust.py`) -> `STRAT_REENTRY_ADAPTIVE=false` LIVE (kraken_bot repornit).
Re-rulat 31 iul pe date PROASPETE: **FIX 6 / tie 8 / adaptiv 0** — FIX nu pierde in NICIO
fereastra, inclusiv sub-ferestrele bull (unde doar egaleaza, reintrarea nu se declanseaza).
Decizia `false` ramane solida, ne mai regim-dependenta. #6 (DCA_DROP 1.25) + #7 (TP 5.0)
re-rulate 31 iul (`backtest.py --mode sweep`, date proaspete): 4h/120z = 0 cicluri (in bull
puternic entry-ul discount market-0.8% nu se umple -> fara semnal, artefact de fill); 1h/30z
declin = TP mai mic/drop mai mare marginal mai bune, DAR toate pierd (-2.8..-3.4%), diferente
mici, pur regim-de-declin (bull favorizeaza TP mare, cf #7 pe 2 regimuri). NICIO schimbare
justificata — valorile live raman alegerea robusta amortizata.

**✅ RE-CONFIRMAT 11 aug (date proaspete, +11 zile) — schimbarile kraken_bot tin:**
- SL 12.5 vs 7 (`scratchpad/sl_sweep.py`): SL=7 pierde in bull (-1% vs +10% la lat/off); SL lat
  (12.5) capteaza upside-ul revenirilor. Ramane corect.
- Reintrare STOP-aware (`scratchpad/reentry_sl_backtest.py`): NOU vs VECHI = +0.74 pct in declin,
  blocked_ticks 259->210 (mai putin stranding); neutru in bull. Ajuta, nu strica.
- Reintrare adaptiva vs FIX (`scratchpad/reentry_robust.py`): FIX 5 / tie 8 / adaptiv 1 -> FIX
  castiga/egal 13/14. `reentry=false` ramane corect.

**Filtru de trend rtrade (11 aug, LIVE) — validare pe date reale (`scratchpad/tao_regime_analysis.py`):**
Din 32 fill-uri TAO (40z): doar **16% in trend clar** (|chg 1h|>1%; 4/5 = vanzari in miscare =
cazul advers), **84% in range**. Deci filtrul e BINE TINTIT (prinde subsetul advers de trend,
NU supra-restrictioneaza cele 84% range) dar impactul e MODEST. rtrade NU se poate backtesta
curat (motorul nu simuleaza fill-uri limit).
CORECTIE (11 aug): ipoteza initiala "fee-churn" era GRESITA — RTRADE_FOLLOWUP_OFFSET_PCT=0.01
se foloseste ca (1+0.01) = **+1.00%** (NU 0.01%), deci marja de flip ACOPERA deja comisioanele
(~0.15%). Pierderea TAO NU e din fee-churn; "churn-ul" de la 43s era plasare/anulare de ordine
(fara comision, doar fill-urile au comision) + vanzarea desperata in trend (cazul advers real).
Ala e adresat de filtrul de trend + followup-ul trend-aware (deja live). Nicio schimbare la flip.

---

## Prioritate MEDIE

| # | Fisier / bot | Variabila | Valoare azi | Status | Grid propus (pas) |
|---|---|---|---|---|---|
| 10 | `kraken/config.env` | `STRAT_ORDER_TTL_MIN` | 10 min | 🔴 (verificat 28-29 iul: `kraken/backtest.py::simulate()` NU modeleaza deloc reasezarea de ordine neexecutate — TTL n-ar avea niciun efect masurabil azi) | {5, 7.5, 10, 15, 20} min — necesita EXTINDEREA motorului (mecanism nou de simulare a ordinelor neexecutate/reasezate), NU adaugata unsupervised peste noapte; de facut cu review. |
| 11 | `kraken/config.env` | `STRAT_STOP_LOSS_PCT` | 7% | 🔴 | {5, 6, 7, 9, 11}% |
| 12 | `kraken/config.env` | `STRAT_ENTRY_DISCOUNT_PCT` | 0.8% | 🟢 **RAMANE 0.8%** | Testat 28-29 iul (kraken/backtest.py --mode single, HYPEUSD, aceleasi 2 regimuri ca #6-7): disc=0.8 (live) e CEL MAI BUN pe AMBELE — bull 120z (+6.74% vs +4.94% urmatorul) SI decline 30z (-2.18%, cel mai putin negativ din 5, aproape egal cu 0.3=-2.32%). Decisiv, nicio schimbare. |
| 13 | `monitortrades_config.env` | `MT_SELL_SAFEBACK_HOURS` | 2h | 🟡 FARA DIFERENTIERE (28 iul) | Sweep {1,1.5,2,3,4}h pe istoric complet (329z, BTC+TAO): rezultat IDENTIC bit-cu-bit pe toate 4 valori (BTC buys=39/sells=26/net=-272.83; TAO buys=16/sells=16/net=+421.54). Plumbing verificat corect (env->constanta modulului, testat direct). Gap-urile reale intre evenimente nu cad aproape de niciun prag testat in acest istoric — parametrul nu diferentiaza nimic AICI, dar asta nu inseamna ca n-are efect in alt regim/istoric. |
| 14 | `monitortrades_config.env` | `MT_BUY_SAFEBACK_HOURS` | 48h | 🟡 FARA DIFERENTIERE (28 iul) | Acelasi sweep/aceeasi cauza ca #13 — {24,36,60,72}h dau rezultate IDENTICE cu #13 (inclusiv intre ele). Vezi nota #13. |
| 15 | `instruments.conf` `[BINANCE_BTC/TAO]` `mt.hardtp` / `mt.hardtp_fraction` (per-instrument, monitortrades.py:447; fallback global in `monitortrades.conf`) | `hard_tp` / `fraction` | 17% / 0.5 | 🟢 **RAMANE 17/0.5** | Testat 28 iul (pilot dry-run): **INERT pe acest istoric** — hard-TP nu se armeaza (pretul n-a urcat +12%..+24%), toate valorile dau rezultate IDENTICE (BTC net -274.22, TAO +152.69 pe tot gridul). Nimic de reglat pe date unde parametrul nu se declanseaza. A si expus un bug de guardrail in pilot (max() pe egalitate "aplica" fals primul din grila) — REPARAT: marja min vs valoarea curenta pe ambele ferestre. |
| 16 | `instruments.conf` `[BINANCE_BTC/TAO]` | `mt.maxage_days` | 7 / 17 | 🟢 **RAMANE 7/17** | Testat 28 iul (pilot dry-run): BTC castigator diferit intre ferestre (10 vs 14) → respins ca zgomot; TAO castigator=17=valoarea curenta → deja optim. Niciun semnal confirmat. |
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
3. ~~**#6-7** (kraken DCA/TP ca valori fixe)~~ — FACUT (28 iul, kraken/backtest.py,
   2 regimuri HYPEUSD): #7 TP RAMANE 5.0 (cel mai bun pe ambele, decisiv);
   #6 DCA drop APLICAT 1.0→1.25 (media, semnal modest dar consistent pe ambele
   ferestre — return + drawdown). kraken_bot repornit, resume corect (qty 22.89),
   DCA -1.25% activ.
4. **NOU gasit (#6-7 sweep)**: `STRAT_STOP_LOSS_PCT`=7% ar putea fi PREA STRANS —
   in sweep-ul default sl=15 a dominat sl=8 pe fereastra bull. DAR: date one-sided
   (bull run, fara crash real care sa testeze riscul unui stop larg) → NU schimbat.
   De investigat separat, prudent, cand avem si o fereastra cu crash.
5. ~~**#15-16** (hard-TP + maxage per instrument, monitortrades)~~ — FACUT (28 iul,
   pilot `--only maxage,hardtp --dry-run`): ambele RAMAN pe valorile actuale.
   #15 hard-TP INERT pe istoric (nedeclansat, toate valorile identice); #16 maxage
   fara semnal (BTC zgomot intre ferestre, TAO deja optim). Bonus: dry-run-ul a
   prins un fals-pozitiv de guardrail in scheduled_pilot (tie-break pe parametru
   inert) — REPARAT cu marja minima vs valoarea curenta (MIN_EDGE_MARGIN_USD).
6. Restul, dupa ce acestea arata daca merita continuat efortul.

**Limitare metodologica kraken (#6-7)**: spre deosebire de tradeall/monitortrades
(arhiva JSONL de 329 zile, ferestre independente), kraken/backtest.py ia OHLC
LIVE din API (~720 bare recente) — nu putem face ferestre vechi INDEPENDENTE.
Cele 2 "regimuri" (120z bull / 30z decline) se SUPRAPUN (30z = coada celor 120z).
De asta schimbarile kraken sunt amortizate (media) si modeste, nu agresive.

---

## Motoare OHLC hyperliquid/212 — rulate 28 iul (existau, dar nerulate serios)

`hyperliquid/backtest.py --mode sweep` (HYPE, 90z, buy&hold +38.2%) si
`212trading/backtest.py --mode sweep` (NVDA/SPCX/RGNT, 2 ani):
- HYPE: top +18.23% (tp=1.5/drop=1.0) — SUB buy&hold (+38.2%), tipar normal DCA+TP
  pe piata puternic in crestere (iese cu profit, rateaza restul urcarii).
- NVDA (buy&hold +76.4%): top doar +1.38% — mult SUB buy&hold, acelasi tipar.
- SPCX (buy&hold **-27.6%**): top **-1.15%** — BATE clar buy&hold (pierde mult mai putin).
- RGNT (buy&hold **-76.1%**): top **-10.58%** — BATE masiv buy&hold (strategia DCA+TP
  a conservat capital semnificativ pe un crash de -76%).

**Tipar clar**: pe active care SCAD mult, DCA+TP (ia profit, nu tine tot drumul in
jos) bate decisiv buy&hold; pe active care CRESC mult, ramane sub (cost de
oportunitate normal al oricarui take-profit).

**Caveat metodologic important**: grila de sweep din `212trading/backtest.py`
(`tp∈{1,1.5,2,3,5}%, drop∈{1,2,3,5}%`) e HARDCODATA la procente MICI — complet
DISJUNCTA de config-ul LIVE al RGNT (`STRAT_TAKEPROFIT_PCT=35, STRAT_DCA_DROP_PCT=40`,
explicit gandit pt miscari parabolice). Deci rezultatele de mai sus NU valideaza
si NU invalideaza setarile live RGNT — testeaza un regim de procente irelevant
pt acest instrument. Follow-up real (nefacut inca): extins sweep-ul in jurul
valorilor live (tp 20-45%, drop 25-50%) pt RGNT specific.

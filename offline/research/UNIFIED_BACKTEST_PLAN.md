# Plan: framework de backtest unificat — DE DISCUTAT, nu implementat

An answer to the question from the session: "I would like a single backtest for all
the modules, where I just set the parameter or module to test; the ranges should come from
configul modulului, poate printr-un comentariu structurat deasupra fiecarui
the parameter — or do you have a more elegant idea, with less code and information that is
putin duplicata?"

Concluzie scurta: DA la ideea de uniformizare, dar NU printr-un singur motor
simulation, and NOT through parseable comments in every config file.
The recommendation: 2 engines (already separate, and they stay separate) plus 1 generic CLI above them
+ 1 SINGUR fisier declarativ cu rangurile (nu comentarii imprastiate in N
formate de config diferite). Detalii mai jos.

---

## 1. De ce NU un singur motor de simulare

Din ce am gasit lucrand la #1/#2 azi, exista deja, de facto, DOUA paradigme
ireconciliabile de simulare in acest repo:

| | **Fleet** (tradeall.py, monitortrades.py) | **Boti pe pozitie** (kraken, hyperliquid, t212) |
|---|---|---|
| Motor existent | `offline.backtests.tradeall.run_backtest()` | `kraken/backtest.py::simulate()` |
| Time unit | TICK (a continuous price, ~1-7 min per tick from the archive) | OHLC BAR (1h/4h/1d) |
| Stare | `PriceWindow`/`TrendState`/`WindowAnalyzer` — ferestre glisante, trend continuu | `qty/cost/dca/last_open` — masina de stari DCA/TP/SL discreta |
| Decision | slope/gradient vs a threshold, over a window | price vs average*(1±%), on the bar close |
| Simbol/pereche | multi-simbol, coordonat (`TrendCoordinator`) | UN simbol per instanta de bot |

Fortarea celor doua in ACELASI motor ar insemna fie (a) sa transformi bare
OHLC in pseudo-tick-uri (pierzi fidelitate: strategia reala kraken evalueaza
pe close de bara, nu pe fiecare tick), fie (b) un singur fisier cu
`if bot_type == "fleet": ... else: ...` care ar deveni EXACT genul de cod
neclar pe care uniformizarea incearca sa-l evite. Cele doua motoare de azi
are already CORRECT and validated (kraken/backtest.py now also has the re-entry
barrier, after today's merge) — the problem is not that there are two, it is that
n-au o "fatada" comuna deasupra.

**Recomandare: pastreaza 2 motoare, unifica doar STRATUL DE DEASUPRA lor**
(CLI, generare grid, raportare) — vezi §3.

---

## 2. De ce NU comentarii structurate parseabile in config

Ideea (un comentariu deasupra fiecarui parametru, intr-un format pe care
backtest-ul sa-l parseze, ex. `# SWEEP: 0.5,1.0,1.5,2.0,2.5`) e atragatoare
la prima vedere, dar are 3 probleme concrete, observate DEJA in acest repo:

1. **3 formate de config diferite, azi**: `.env` (KEY=VALUE, `tradeall_config.env`
   etc.), `monitortrades.conf` (format propriu, `cheie = valoare`), `instruments.conf`
   (INI, sectiuni `[NUME]`). Un parser de comentarii ar trebui sa stie sa
   citeasca toate 3 — exact opusul lui "mai putin cod, mai uniform".
2. **Not everything worth testing already has an env var**: 3 of the Kalman constants
   (`CONF_ENTER`, `MIN_VEL_PCT_MIN`, `GAP_RESET_SEC`, vezi
   `BACKTEST_CANDIDATES.md`) are hardcoded, with NO config line where
   sa atasezi un comentariu. Un mecanism bazat pe "comentariu deasupra
   the parameter in config" only covers them once you extract them first.
3. **Comentariile deriveaza tacut** — chiar in sesiunea asta am gasit si
   reparat DOUA comentarii stale (`tradeall.py`: "SHADOW observational" cand
   de fapt initia ordine reale; `instruments_config.py`: pretindea consumatori
   that did not exist). A comment that is BOTH documentation AND configuration
   parseabila mosteneste exact aceeasi fragilitate — nimic nu garanteaza ca
   ramane sincron cu valoarea reala de langa el.

**Alternativa propusa: UN SINGUR fisier declarativ**, nu comentarii
imprastiate in N formate. Vezi `offline/research/BACKTEST_CANDIDATES.md` (deja scris
azi) — as extinde EXACT acel fisier (nu unul nou) cu un bloc masina-citibil
(YAML/JSON in a fenced code block, or a `.json` sidecar next to it) that
contains, for each table row, exactly what a runner needs in order to
stie ce sa ruleze:

```yaml
# exemplu ilustrativ, NU implementat inca
- id: mt_btc_gain
  module: monitortrades
  engine: fleet
  target: {file: instruments.conf, section: BINANCE_BTC, key: mt.gain}
  range: {min: 5.0, max: 9.0, values: 5}   # → 5,6,7,8,9
- id: kraken_dca_drop
  module: kraken
  engine: position
  target: {file: kraken/config.env, key: STRAT_DCA_DROP_PCT}
  range: {min: 0.5, max: 2.0, values: 5}   # → 0.5,0.75,1.0,1.5,2.0
```

De ce e mai simplu decat comentarii-in-config:
- **UN loc, un format** — nu 3 parsere pt 3 formate de config.
- **It covers what has not been extracted yet** (`target` may be missing or empty for an idea
  neconfigurata inca — runner-ul stie sa refuze/avertizeze clar, nu sa
  ghiceasca dintr-un comentariu absent).
- **Nu dubleaza informatia de doua ori "in cod"** — fisierul de config ramane
  100% curat (doar valoarea LIVE, cum e azi); rangul de test traieste UNDE
  traieste deja azi lista de candidati, doar structurat in loc de proza.
- Editabil de mana la fel de usor ca tabelul markdown de azi (ramane in
  ACELASI fisier, doar cu un bloc de date langa proza).

---

## 3. Ce ar insemna, concret, "un singur backtest, setez doar parametrul"

Un CLI generic, subtire, deasupra celor 2 motoare + fisierul de rangs de mai
sus:

```
python3 offline/research/backtest_runner.py --param mt_btc_gain
python3 offline/research/backtest_runner.py --param kraken_dca_drop --symbol HYPEUSD
```

What it would do (schematically, still a plan, not code):
1. Cauta `--param` in blocul declarativ din `BACKTEST_CANDIDATES.md`.
2. From `engine: fleet|position` it knows which of the 2 engines to use.
3. Genereaza grid-ul (`min/max/values`, sau lista explicita) — ≤5 valori,
   aceeasi logica de generare pt AMBELE motoare (asta e partea genuin
   "unificata": generarea grid-ului + rularea in bucla + raportarea rezultatelor
   intr-un tabel comun, NU simularea insasi).
4. Pt fiecare valoare din grid, apeleaza un adaptor MIC, specific motorului:
   - adaptor `fleet`: construieste un `threshold_provider`/monkeypatch potrivit
     si cheama `offline.backtests.tradeall.run_backtest(..., threshold_provider=...)`
     (hook-ul adaugat azi la #2).
   - adaptor `position`: seteaza cheia in dictul `P` si cheama
     `kraken.backtest.simulate(ohlc, P, ...)` (extins azi la #1).
5. Colecteaza `pnl.json` din fiecare rulare, tipareste un tabel comparativ
   (valoare | net_total | buy_hold | cicluri/tranzactii | maxDD) — acelasi
   format pt oricare param, indiferent de motor.

Cod nou necesar: 1 CLI mic (bucla sweep + tabel) + 2 adaptoare mici (fleet,
position) — restul (motoarele, hook-urile) exista deja de azi. Nu e un
rescris, e o "fatada" peste ce exista.

---

## 4. Fleet vs Boti — raspuns direct la intrebarea din mesaj

Da, backtest SEPARAT pt flota (tradeall/monitortrades) si pt boti-pozitie
(kraken/hyperliquid/t212) — dar NU doua CLI-uri separate pt utilizator, ci
DOUA ADAPTOARE sub ACELASI CLI (`--param X` alege automat motorul corect prin
the `engine:` field in the declaration). From your perspective as a user, it stays
"un singur backtest, setez parametrul" — separarea reala (fleet vs pozitie)
an implementation detail that is hidden, not something you have to choose by hand.

---

## 5. Ce NU rezolva planul asta (limite onest raportate)

- Rtrade and assetguardian still have NO backtest engine at all (unlike
  deosebire de kraken/tradeall) — ar avea nevoie de un al treilea adaptor sau
  one of the 2 existing ones has to be extended, once it is decided which paradigm
  potriveste mai bine (rtrade pare mai aproape de "pozitie" — DCA-like — desi
  with concurrent BUY and SELL, something no engine models today).
- Comparability BETWEEN different modules (e.g. "which is better, a 7% gain
  on BTC or a K of 2.0 on Kraken re-entry") makes no direct sense — each
  parameter is compared only against ITS OWN variants, not across modules. The plan above
  nu incearca sa rezolve asta (nici n-ar trebui).
- Fisierul declarativ propus (§2) tot cere disciplina umana sa fie actualizat
  cand se schimba o valoare live — reduce riscul de derapaj (un singur loc,
  not N comments), but it does not remove it entirely. There could be a simple test
  that checks that every `target.key` in the declaration really exists in
  fisierul de config referit (evita cel putin typo-uri/chei sterse).

---

## Intrebari pt discutie — DECISE 28 iul (user)

1. Fisierul declarativ (§2): **bloc in `BACKTEST_CANDIDATES.md`** (nu YAML/JSON
   sidecar) — un singur loc, langa tabelul de proza, fara dublare. E util in
   principal ca sa alimenteze CLI-ul (§3); pana atunci ramane un registru
   structurat, nu o necesitate.
2. rtrade/assetguardian: **in afara deocamdata.** rtrade cere motor nou (BUY/SELL
   concurent pe acelasi simbol, nemodelat de niciun motor de azi); assetguardian
   are valoare de backtest mica. Le includem dupa ce tiparul de adaptor se
   valideaza pe cele 2 din faza 1.
3. CLI-ul (§3): **mai tarziu**, dupa inca 1-2 cazuri (ex. #15-16) — ramanem pe
   individual scripts so that the adapter emerges from experience rather than from design
   priori.

---

## 9. Arhitectura pe 2 masini (DECISA 28 iul, user) — backtest pe "dev", aplicare pe productie

Motivatie: rularea backtestelor pe masina de PRODUCTIE concureaza pt CPU cu
tradingul live (exact ce a facut ~20-90 min/ciclu prohibitiv pt "de mai multe
ori pe zi"). Mutarea pe o masina-oglinda (**"dev"**) rezolva asta si a fost
blocajul real al periodizarii. Trei componente:

**A. Masina dev ruleaza backtestele.** Citesc `cachedb/cache_price_{symbol}.jsonl`
(the price archive) plus the LIVE values from config (the baseline). So dev needs
de ambele proaspete → **sync productie→dev via git** (decizie user: git, nu
ssh/rsync — da audit-trail gratis + reversibil). dev face pull la arhiva+config.

**B. Scriere inapoi = POARTA de propunere, nu scriere directa** (guardrail-urile
impartite pe cele 2 masini):
- **dev** runs the grid plus the confirmation over 2 windows, producing only
  *castigatorul confirmat* per cheie (semnalul curat), il propune (commit git).
- **productia** primeste propunerea si APLICA: media cu valoarea live reala
  (authoritative there), a 7-day rate limit, an audit, and writes the config. dev has NO right
  de scriere directa — aceleasi 5 guardrail-uri raman, doar CPU-ul se muta.

**C. `watchdogfor_cacheandconfig.py`** (decizie user: UN singur .py pt cache SI
config — extind watchdogfor_cache.py existent, NU un fisier separat). Pe langa
cache staleness (today), it looks through the config files and restarts
procesul proprietar cand un config s-a schimbat recent. Generalizeaza
`scheduled_pilot._restart_monitortrades()` + bonus: **prinde si editarile
manual ones** (today you edit a config and have to remember to restart). 3
cerinte ca sa fie robust:
1. **Detectie pe hash de continut, NU pe mtime** (acelasi tipar content-based
   deja folosit pt cache) — altfel o atingere de fisier fara schimbare reala
   da reporniri false.
2. **Harta config→proces** — atentie, unele config-uri au MAI MULTI consumatori:
   `instruments.conf` e citit de monitortrades.py SI tradeall.py → o schimbare =
   repornesti ambele.
3. **Debounce / atomic write** — it restarts only after a complete write, a
   singura data.

Ramane de implementat dupa ce se dau datele de conectare pe dev. Pana atunci:
backtest candidates that run 100% locally (one-off, dry run), see §8/#15-16.

---

## 6. Observatie 23 iul (dupa #1/#2): API-ul de piata are o interfata unificata
that can give both "live now" and "the simulated state at moment X"?

The user's question: do the exchanges have a unified API from which you take either the
LIVE reala acum, fie starea SIMULATA la un moment X — ar fi un pas important
pt backtest consistent?

Raspuns: DA, e directia corecta, si e PARTIAL deja adevarat aici, dar in doua
bucati separate care n-au fost inca unite:

- `providers/market_api.py` (facada `mkt`) unifica deja LIVE-ul **intre
  exchange-uri** (Binance/Kraken/Hyperliquid/T212 raspund la aceleasi apeluri:
  `get_current_price`, `get_orders`, `free_balance`).
- `offline/backtests/tradeall.py`'s `_SimClock` + iteratorul de tick-uri istorice
  already unifies LIVE versus HISTORICAL **for time**, but ONLY for tradeall.py, and NOT
  through the facade — it is a separate loop that rebuilds `PriceWindow`/
  `TrendState` direct din date istorice, ocolind complet `TrendCoordinator`/
  cacheManager (the REAL path by which tradeall.py obtains prices today).

What does NOT exist yet: the `mkt` facade itself having a "replay mode" — that is,
`mkt.get_current_price(symbol)` sa poata raspunde fie "acum", fie "la
the simulated timestamp T", through the SAME call. If it existed, the REAL code of
botilor (nu o reimplementare separata ca `kraken/backtest.py::simulate()`)
ar putea rula neschimbat impotriva istoricului — eliminand complet riscul de
drift between "what the real bot does" and "what the backtest simulates" (exactly
problema gasita azi la #1: bariera de reintrare lipsea din simulare pt ca
simularea era o COPIE, nu codul real).

Limita onesta: unificarea asta rezolva doar latura de "ce spunea piata" —
tot ai nevoie de un broker simulat separat (ca `BacktestBroker`/motoarele
`simulate()` de azi) ca sa decizi "s-ar fi executat ordinul asta la pretul
istoric respectiv" — asta ramane un mecanism DIFERIT, complementar, nu
disappears once the price/time source is unified.

---

## 7. Cerere user: flota (tradeall/monitortrades/rtrade/assetguardian) trebuie
be UNIFORM in its price source (the cache, not live) and its time (from the timestamp
pretului, sau scara simulata a backtestului) — de unde incepem?

I pick 2 modules for PHASE 1 (not all 4 at once), on the criterion of "the least effort
x cea mai mare valoare imediata":

### FAZA 1: `tradeall.py` (formalizeaza ce exista deja) + `monitortrades.py` (nou)

**`tradeall.py` — deja ~70% acolo.** `TrendState`/`PriceWindow` accepta deja
`now_fn` injectabil (asta e EXACT mecanismul de "timpul vine din
simulare" cerut) si `offline/backtests/tradeall.py` deja re-alimenteaza `PriceWindow`
cu preturi istorice in loc de live. Ce lipseste azi: mecanismul e ad hoc,
scris o singura data in `offline/backtests/tradeall.py`, nereutilizabil de altundeva
(hook-ul `threshold_provider` de azi e un prim pas spre generalizare, dar
the price source and the clock stay "sewn into" the `run_backtest()` loop, rather than a
componenta separata, reutilizabila). Faza 1 aici = extrage `_SimClock` +
incarcarea tick-urilor istorice intr-o componenta mica, separata
(`PriceReplaySource`?), NEschimband tradeall.py insusi (deja e suficient de
injectabil).

**`monitortrades.py` — 0% azi, dar cea mai mare valoare.** Nu exista NICIUN
backtest pt el, si `BACKTEST_CANDIDATES.md` a identificat gain/lost per
simbol (`instruments.conf`) ca cel mai valoros candidat NETESTAT din tot
the inventory (#4-5, HIGH priority).

**23 Jul, CONFIRMED (not merely speculated) — the injection seam ALREADY EXISTS, complete:**
- `Instrument.__init__(..., api=None)` — if `api` is not given, it falls back to
  singleton-ul live (`_default_api`); daca E dat, `self._provider =
  self._api.provider_by_name(provider)` uses THAT api. Every method
  (`price()`, `orders()`, `free()`) delegheaza la `self._provider`.
- `instruments_config.load_instruments(path=None, api=None)` si
  `load_for(consumer, path=None, api=None, ...)` propaga DEJA acest `api` mai
  onwards to every `Instrument` built from `instruments.conf`.
- Conclusion: `monitortrades.py` needs NO change AT ALL at the lines where
  it reads price and orders (`inst.price()`, `inst.orders(...)`, `load_for("mt")`)
  — doar construit/injectat un `MarketApi` diferit (unul de REPLAY) la
  pornirea unui backtest. Asta era, de fapt, exact scopul pt care facada asta
  a fost proiectata ("Faza 2a/2b" din docstring-ul `market_api.py` — cineva
  intr-o sesiune anterioara planuise deja acest tip de extensie).
- `MarketDataProvider` are deja un stub `get_price_history(symbol, lookback_h)`
  — dar verificat azi: cele 2 implementari REALE existente (Hyperliquid,
  Kraken) sunt LIVE-ONLY ("ultimele N ore de la time.time() ACUM", bat reteaua
  reala de fiecare data) — bune pt backfill la pornirea unui bot, INUTILIZABILE
  as a replay source (they do not read from a local cache and accept no arbitrary moment T
  in the past). T212 and Binance do not even implement it (they return None).

**Ramane de scris DOAR o piesa noua**: `ReplayMarketDataProvider` (implementeaza
`MarketDataProvider`, reads from `cache_price_{symbol}.jsonl`/`cache_24price_*`,
tine un cursor/ceas intern care avanseaza cu fiecare citire) + injectarea celor
2 `time.time()` din `monitortrades.py` (`get_relevant_trade`,
`monitor_price_and_trade`) printr-un `now_fn` implicit = `time.time`, legat de
the SAME clock that the new provider advances — that way the time really does come "from
timpul pretului obtinut", cum a cerut mesajul, nu dintr-un ceas simulat separat.

Efortul e mult mai mic decat parea initial: 1 fisier nou (provider-ul de
replay) + o injectare minima de ceas in monitortrades.py — NU o rescriere a
the price and order paths, which already work through `api` injection.

**De ce NU rtrade/assetguardian in faza 1:**
- `rtrade.py` runs BUY and SELL on SEPARATE THREADS, concurrently, on
  ACELASI simbol — niciun motor de azi (fleet sau pozitie) modeleaza asta;
  would require a new design, not merely price/time injection.
- `assetguardian.py` evalueaza o singura data la ~54s pe o valoare de
  portofoliu AGREGATA (cache "AssetValue"), nu pe pretul unui simbol — sursa
  lui de "adevar" e alt tip de cache decat cel de pret; injectarea
  timpului/pretului e mai simpla acolo, dar valoarea de backtest e mai mica
  (deja "practic oprit" pe crestere, vezi `BACKTEST_CANDIDATES.md` §exclusii).

They remain PHASE 2, once the pattern (an injectable price source plus an injectable clock)
se valideaza pe cele 2 din faza 1.

### Ce ar insemna concret sursa de pret + ceas unificate (schematic, tot plan)

Two SMALL components, reusable between tradeall and monitortrades:

- **`Clock`**: un obiect cu o metoda, `now() -> float`. Implicit = `time.time`
  (comportament live, neschimbat). In replay: `now()` intoarce timestamp-ul
  the LAST price read from the source below — not a simulated clock that advances
  independent, exact cum a cerut mesajul ("timpul sa vina din timpul pretului
  obtinut") — asta e deja tiparul `_SimClock` din `offline/backtests/tradeall.py`,
  merely generalised so it is not tied to a single file.
- **`PriceSource`**: un obiect cu o metoda, `get_price(symbol) -> float`.
  Implicit = calea live de azi (mkt/cacheManager, neschimbata). In replay:
  reads sequentially from `cache_price_{symbol}.jsonl`/`cache_24price_*.json`,
  avansand `Clock`-ul asociat la fiecare citire.

Ambele module (tradeall, monitortrades) ar primi aceste 2 obiecte prin
injection (a parameter whose default is today's live behaviour), not through
monkeypatch extern — asta e diferenta fata de tiparul de azi din
`offline/backtests/tradeall.py` (care monkeypatch-uieste `ta.po.place_order_smart`
and so on from OUTSIDE) and would make testing more direct and clearer.

### Atentie (acelasi standard ca extragerile de azi)

Any change in `monitortrades.py` itself (not just a harness beside it)
must pass the same test: the default value (without a Clock/PriceSource
custom injectat) trebuie sa reproduca EXACT comportamentul de azi — verificat
numeric, cu teste dedicate, inainte de orice commit. Nu se schimba logica de
decizie, doar SURSA datelor de intrare.

---

## 8. The pilot built on 23 Jul — status and a real TODO found (not merely theoretical)

Pilotul (monitortrades.py, `offline/research/backtest_ranges.py` +
`offline/research/monitortrades_backtest/scheduled_pilot.py`) e construit si validat
manual (dry-run, un parametru). In timpul validarii au iesit la iveala 2
bug-uri REALE (nu ipotetice) in mecanismul de backtest insusi:

1. **`run_replay_backtest.SYMBOLS` era o copie hardcodata** a `instruments.conf`,
   inghetata inainte sa adaugam `mt.buy_budget`/`mt.max_budget` — orice test
   would later run WITHOUT that protection, unnoticed (the fix: read it live,
   la fiecare acces).
2. **`is_trend_up()` citea cache-ul de trend LIVE** (contamina un replay
   istoric cu starea REALA, curenta a pietei — acelasi backtest, rulat de 2
   ori, dadea rezultate diferite). Fix APLICAT: neutralizat determinist
   (`return False`) in timpul backtest-ului.

Fix-ul #2 e o SIMPLIFICARE, nu fidelitate completa — observatie user (23 iul,
evening): `priceAnalysis.py` (which feeds the trend signal from the
Binance in productie) ar trebui sa ruleze SINCRON cu `tradeall.py`/replay-ul
historically in a backtest, not merely neutralised. That is: `priceAnalysis.py` would
avea nevoie de ACELASI tratament de injectare pret+ceas ca `monitortrades.py`
azi, calculand trendul DIN istoricul redat (acelasi ceas, aceleasi tick-uri),
not from a separate live feed. That would make `is_trend_up()` reflect what the bot
would TRULY have seen at that historical moment, rather than just "no signal".

**Ramane TODO explicit, NU implementat inca** — e o bucata de lucru comparabila
ca marime cu ce am facut azi pt monitortrades.py (Instrument/MarketDataProvider),
dar pt priceAnalysis.py, care azi n-are NICIUN punct de injectare. Pana atunci,
any backtest that uses is_trend_up() (so any monitortrades backtest)
ramane cu simplificarea "neutru" — corect etichetata, dar incompleta.

**Re-validare dupa fix-uri**: `instruments.conf` de azi (buy_budget=250,
max_budget=3500 for TAO) gives STABLE and identical results for max_budget between
1500-5000 (net -$170.83 vs buy&hold -$426.06) — instabilitatea vazuta inainte
de fix (acelasi max_budget dand +$3016 apoi -$5279) era in intregime bug-ul
#2, nu sensibilitate reala la parametru. Nicio schimbare de config necesara.

**Cadenta scheduler-ului**: o rulare completa pt 1 parametru (4 valori x 2
ferestre) a durat ~5 min pe acest hardware; toti cei 4 parametri pilot ar
insemna ~20-90 min per ciclu — prea lent pt "de mai multe ori pe zi" fara
ajustari (fereastra mai scurta pt rulari de rutina? rotatie prin parametri in
loc de toti deodata? de decis inainte de a pune pe cron).

**The pilot was RUN in full (23 Jul, evening)**: 3 of the 4 keys were NOT confirmed (a winner
diferit intre cele 2 ferestre — guardrail corect, respins ca zgomot). 1
confirmata clar: TAO `mt.lost` — 5.6% a castigat pe AMBELE ferestre (edge vs
buy&hold 259.85/329.0, fata de 84.07/208.21 la 4.9% actual). Aplicat automat
(medie): 4.9 -> 5.25. Toate guardrail-urile au functionat ca proiectat.

**TODO investigat, NU implementat (23 iul, seara — cerere user)**: observatie
ca `priceAnalysis.py` si `tradeall.py` ar trebui sa fie sincrone intr-un
backtest. Verified by grep: it is NOT `priceAnalysis.py` that writes the trend signal
citit de `is_trend_up()` — e chiar `tradeall.py` (`TrendCoordinator.evaluate()`
scrie `gradient_recent`/`final_trend` in `cacheManager.get_short_trend_manager()`,
un singleton cu memorie IN-PROCES + fallback pe fisier `cache_instant_trend.json`
for any process that has nothing in memory yet — that explains exactly
contaminarea gasita: procesul de backtest, fiind un proces NOU, cade pe
fisierul de pe disc, care e scris LIVE de tradeall.py real). Fix-ul corect ar
insemna: alimentat un `PriceWindow` (acelasi tip deja folosit de
`offline/backtests/tradeall.py`) cu ACELASI istoric replay-uit ca `ReplayMarketDataProvider`,
calculand `gradient_recent` real din date istorice, publicat intr-un
snapshot IZOLAT (nu fisierul global) pt ca `is_trend_up()` sa-l citeasca.
Tractabil (piesele exista deja), dar netestat — amanat deliberat (nu la ora
asta, fara sa poata fi validat riguros ca restul sesiunii) in favoarea
the backtest runs already built and validated, left to run overnight.

---

## 10. Strategia de cautare: OFAT vs grid complet (EXTENSIE, 28 iul — NU implementat)

The user's question: do I take the parameters one at a time, or all combinations? An estimate
data: "4 params × 3 sample = 12 rulari".

**Corectie de aritmetica** (capcana e combinatoriala, nu liniara):
- **One-at-a-time (OFAT / coordonate)** — schimbi UN param, ceilalti raman fixati
  pe valoarea live: `4 params × 3 sample = 12`. Cifra de 12 e corecta DOAR aici.
- **Grid complet (produs cartezian)** — toate combinatiile: `3⁴ = 81`. Cu 5 sample
  → `5⁴ = 625`. La ~37s/rulare (2 ferestre): OFAT ~9 min/simbol; grid 81 ~100
  min/simbol; grid 625 ~13h/simbol. Explodeaza rapid.

| | OFAT | Grid complet |
|---|---|---|
| Rulari (4p × 3s) | 12 | 81 |
| Does it catch interactions between parameters? | NO | YES |
| Interpretabil ("care knob e prost setat") | DA | greu (verdict cuplat) |

**De ce conteaza interactiunile aici (nu teoretic):** `max_budget=5000` a dat
+$3016 intr-o configurare si -$5279 in alta, pe ACELASI istoric — parametrii se
cupleaza tare. OFAT poate gasi un castigator pt param A bun DOAR la valoarea
curenta a lui B; daca B se schimba, optimul lui A se muta.

**Recomandare (pragmatica, bani reali):**
1. **Default = OFAT** — exact ce face pilotul azi. Ieftin, verdict clar per param,
   se preteaza la rotatie pe dev (un param/rulare).
2. **A full grid ONLY on known coupled pairs** (e.g. `gain × lost`, or
   `hardtp × hardtp_fraction`) — 3×3=9 combinatii, ieftin, prinde fix interactiunea
   pe care OFAT o rateaza. Grid pe toti 4 deodata → evitat (81+).
3. **O rulare de confirmare la config-ul propus COMPLET inainte de aplicare** —
   chiar cu OFAT + aplicare independenta per param, ajungi la o combinatie
   nebacktestata impreuna; confirm-o o data inainte sa scrii config.
4. **Ranguri stranse (3-4 sample)**, incluzand mereu valoarea live in grila.

**Mitigare deja existenta:** rate-limit-ul de 7 zile per parametru din
scheduled_pilot inseamna ca in productie schimbi oricum UN param odata (fiecare
schimbare confirmata asteapta 7 zile) — nu aplici 4 mutari simultan orb. Riscul
"config comun netestat" e marginit natural.

**Extensie propusa (NU implementata):** un mod `--grid gain,lost` in
scheduled_pilot that takes the Cartesian product ONLY over the given pair (the rest stay
OFAT/fix). Mic, optional, de adaugat pe dev cand implementam faza dev/prod.

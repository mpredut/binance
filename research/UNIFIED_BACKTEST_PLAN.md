# Plan: framework de backtest unificat — DE DISCUTAT, nu implementat

Raspuns la intrebarea din sesiune: "as vrea un singur backtest pt toate
modulele, doar setez parametrul/modulul de testat; rangurile sa vina din
configul modulului, poate printr-un comentariu structurat deasupra fiecarui
parametru — sau ai o idee mai eleganta, cu mai putin cod si informatie mai
putin duplicata?"

Concluzie scurta: DA la ideea de uniformizare, dar NU printr-un singur motor
de simulare si NU prin comentarii parseabile in fiecare fisier de config.
Recomand: 2 motoare (deja separate, ramân separate) + 1 CLI generic deasupra
+ 1 SINGUR fisier declarativ cu rangurile (nu comentarii imprastiate in N
formate de config diferite). Detalii mai jos.

---

## 1. De ce NU un singur motor de simulare

Din ce am gasit lucrand la #1/#2 azi, exista deja, de facto, DOUA paradigme
ireconciliabile de simulare in acest repo:

| | **Fleet** (tradeall.py, monitortrades.py) | **Boti pe pozitie** (kraken, hyperliquid, t212) |
|---|---|---|
| Motor existent | `tradeall_backtest.run_backtest()` | `kraken/backtest.py::simulate()` |
| Unitate de timp | TICK (pret continuu, ~1-7 min/tick din arhiva) | BARA OHLC (1h/4h/1z) |
| Stare | `PriceWindow`/`TrendState`/`WindowAnalyzer` — ferestre glisante, trend continuu | `qty/cost/dca/last_open` — masina de stari DCA/TP/SL discreta |
| Decizie | slope/gradient vs prag, pe fereastra | pret vs prag_mediu*(1±%), pe close de bara |
| Simbol/pereche | multi-simbol, coordonat (`TrendCoordinator`) | UN simbol per instanta de bot |

Fortarea celor doua in ACELASI motor ar insemna fie (a) sa transformi bare
OHLC in pseudo-tick-uri (pierzi fidelitate: strategia reala kraken evalueaza
pe close de bara, nu pe fiecare tick), fie (b) un singur fisier cu
`if bot_type == "fleet": ... else: ...` care ar deveni EXACT genul de cod
neclar pe care uniformizarea incearca sa-l evite. Cele doua motoare de azi
sunt deja CORECTE si validate (kraken/backtest.py acum are si bariera de
reintrare, dupa merge-ul de azi) — problema nu e ca sunt doua, problema e ca
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
2. **Nu tot ce merita testat are deja un env var**: 3 din constantele Kalman
   (`CONF_ENTER`, `MIN_VEL_PCT_MIN`, `GAP_RESET_SEC`, vezi
   `BACKTEST_CANDIDATES.md`) sunt hardcodate, FARA nicio linie de config unde
   sa atasezi un comentariu. Un mecanism bazat pe "comentariu deasupra
   parametrului din config" nu le acopera decat dupa ce le extragi intai.
3. **Comentariile deriveaza tacut** — chiar in sesiunea asta am gasit si
   reparat DOUA comentarii stale (`tradeall.py`: "SHADOW observational" cand
   de fapt initia ordine reale; `instruments_config.py`: pretindea consumatori
   care nu existau). Un comentariu care e SI documentatie SI configuratie
   parseabila mosteneste exact aceeasi fragilitate — nimic nu garanteaza ca
   ramane sincron cu valoarea reala de langa el.

**Alternativa propusa: UN SINGUR fisier declarativ**, nu comentarii
imprastiate in N formate. Vezi `research/BACKTEST_CANDIDATES.md` (deja scris
azi) — as extinde EXACT acel fisier (nu unul nou) cu un bloc masina-citibil
(YAML/JSON intr-un fenced code block, sau un `.json` sidecar langa el) care
sa contina, pt fiecare rand din tabel, exact ce ii trebuie unui runner ca sa
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
- **Acopera si ce nu e inca extras** (`target` poate lipsi/fi gol pt o idee
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
python3 research/backtest_runner.py --param mt_btc_gain
python3 research/backtest_runner.py --param kraken_dca_drop --symbol HYPEUSD
```

Ce ar face (schematic, tot plan — nu cod):
1. Cauta `--param` in blocul declarativ din `BACKTEST_CANDIDATES.md`.
2. Din `engine: fleet|position` stie care din cele 2 motoare sa foloseasca.
3. Genereaza grid-ul (`min/max/values`, sau lista explicita) — ≤5 valori,
   aceeasi logica de generare pt AMBELE motoare (asta e partea genuin
   "unificata": generarea grid-ului + rularea in bucla + raportarea rezultatelor
   intr-un tabel comun, NU simularea insasi).
4. Pt fiecare valoare din grid, apeleaza un adaptor MIC, specific motorului:
   - adaptor `fleet`: construieste un `threshold_provider`/monkeypatch potrivit
     si cheama `tradeall_backtest.run_backtest(..., threshold_provider=...)`
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
campul `engine:` din declaratie). Din perspectiva ta (utilizator), ramane
"un singur backtest, setez parametrul" — separarea reala (fleet vs pozitie)
e un detaliu de implementare ascuns, nu ceva ce trebuie sa alegi manual.

---

## 5. Ce NU rezolva planul asta (limite onest raportate)

- Rtrade si assetguardian nu au inca UN motor de backtest deloc (spre
  deosebire de kraken/tradeall) — ar avea nevoie de un al treilea adaptor sau
  de extins unul din cele 2 existente, dupa ce se decide care paradigma se
  potriveste mai bine (rtrade pare mai aproape de "pozitie" — DCA-like — desi
  cu BUY si SELL concurente, ceva ce niciun motor de azi nu modeleaza).
- Comparabilitate INTRE module diferite (ex. "care e mai bun, un gain de 7%
  pe BTC sau un K de 2.0 pe reentry Kraken") nu are sens direct — fiecare
  param se compara doar cu variantele LUI, nu intre module. Planul de mai sus
  nu incearca sa rezolve asta (nici n-ar trebui).
- Fisierul declarativ propus (§2) tot cere disciplina umana sa fie actualizat
  cand se schimba o valoare live — reduce riscul de derapaj (un singur loc,
  nu N comentarii), dar nu il elimina complet. Ar putea exista un test simplu
  care verifica ca fiecare `target.key` din declaratie chiar exista in
  fisierul de config referit (evita cel putin typo-uri/chei sterse).

---

## Intrebari pt discutie (nu decizii luate)

1. Fisierul declarativ (§2): YAML separat, JSON separat, sau bloc in
   `BACKTEST_CANDIDATES.md`? (recomand: bloc in acelasi .md, ca sa nu se
   dedubleze intre proza si date)
2. rtrade/assetguardian: le lasam in afara acestui efort (backtest separat,
   mai tarziu) sau le includem de la inceput intr-un al treilea adaptor?
3. CLI-ul (§3): merita sa existe acum, sau ramanem cu scripturi individuale
   (ca azi) pana quand se acumuleaza mai multe cazuri si tiparul de adaptor
   devine mai clar din experienta, nu din design a priori?

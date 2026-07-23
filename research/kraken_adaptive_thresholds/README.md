# Investigație: praguri adaptive (volatilitate) pe Kraken — merită trecute din shadow în live? (22-23 iul 2026)

Întrebare: `kraken/strategy.py` calculează deja (din 17 iul) praguri adaptive
pe volatilitate pentru **reintrare** (`SHADOW_K_REENTRY`) și **DCA**
(`SHADOW_K_DCA`), dar DOAR ca linie de log comparativ ("[SHADOW] prag fix X
vs adaptiv Y") — niciodată folosite pentru decizia reală, care rămâne pe
procentul fix din `config.env`. Merită promovate la decizie reală?

**Nimic din codul de producție (`kraken/strategy.py`, `config.env`) nu a fost
modificat** — ambele scripturi de mai jos sunt teste izolate, doar citesc
date publice (Kraken OHLC API) și rulează o simulare separată.

## Descoperire metodologică importantă

Nici `kraken/backtest.py`, nici `kraken/backtest_adaptive.py` (unelte deja
existente în repo) nu modelează bariera de reintrare
(`STRAT_REENTRY_DROP_PCT`/`STRAT_REENTRY_TOLERANCE_PCT`) — după ce o poziție
se închide (take-profit sau stop-loss), simulatoarele originale reintră
IMEDIAT la următoarea bară, fără nicio așteptare. Strategia REALĂ așteaptă
explicit ca prețul să scadă sub `last_sell_price*(1-reentry_pct%)` înainte
să reintre. `verify_adaptive_reentry.py` adaugă acest mecanism lipsă (fidel
formulei `botcore.diff_percent`/`are_close` pentru toleranță).

## Rezultate (HYPEUSD, 60 de bare/oră, ~30 de zile — limita API-ului public
Kraken pentru date istorice; parametri = valorile REALE din `kraken/config.env`,
nu default-urile scripturilor originale — greșeala prinsă într-o sesiune
anterioară a fost exact folosirea unor parametri ghiciți, care a inversat
concluzia)

### 1. Prag de DCA (`verify_adaptive_dca.py`) — K_DCA × vol_1h vs STRAT_DCA_DROP_PCT=1.0%

| variantă | prag mediu | TOTAL | realizat | cicluri | win-rate | maxDD |
|---|---|---|---|---|---|---|
| FIX (live azi) | 1.0% | **+1.90%** | +164,45$ | 6 | 83% | 175,30$ |
| adaptiv-shadow | 0.76% (0.36–2.29%) | +0.44% | +128,56$ | 7 | 86% | 251,84$ |

**Fixul câștigă clar** — pragul adaptiv, în medie mai mic (mai permisiv la
DCA), a dus la o poziție finală cu 50% mai mare (32,3 vs 21,4 unități) și un
drawdown mult mai mare, fără o îmbunătățire compensatorie de randament.

### 2. Prag de reintrare (`verify_adaptive_reentry.py`) — K_REENTRY × vol_1h vs STRAT_REENTRY_DROP_PCT=2.2%

| variantă | prag mediu | TOTAL | realizat | cicluri | win-rate | maxDD |
|---|---|---|---|---|---|---|
| FIX (live azi) | 2.2% | +2.20% | +123,29$ | 6 | 83% | 165,41$ |
| adaptiv-shadow | 1.51% (0.72–4.58%) | **+3.20%** | **+155,08$** | 7 | 86% | **153,51$** |

**Adaptivul câștigă clar, pe toate criteriile** — randament total mai mare,
profit realizat mai mare, win-rate mai bun ȘI drawdown mai mic. Pragul
adaptiv a fost mai permisiv (~1.5%) în perioadele liniștite (reintrare mai
rapidă, nu ratează revenirile mici) și mai strict (până la 4.58%) în
perioadele volatile (evită reintrarea prematură într-un fals-fund).

## Concluzie

**Cele două praguri NU trebuie tratate la fel** — intuiția utilizatorului se
confirmă pentru REINTRARE, dar nu pentru DCA:
- **DCA**: rămâne pe pragul FIX (1.0%) — adaptivul a fost testat și pierde.
- **Reintrare**: pragul adaptiv arată o îmbunătățire consistentă pe toate
  criteriile (nu doar randament, ci și risc — drawdown mai mic). Merită
  luat serios în calcul pentru promovare la decizie reală.

**Avertisment onest asupra eșantionului**: doar ~30 de zile / 6-7 cicluri
complete de tranzacționare — mult mai mic decât eșantionul de 329 de zile
din investigația `tradeall.py` (`research/tradeall_trigger_gate/`). Un
rezultat pe atât de puține cicluri poate fi sensibil la 1-2 tranzacții
individuale. Înainte de promovare reală, ar merita fie (a) extinderea
ferestrei de test dacă apare o sursă de date mai lungă (istoricul de
tranzacții reale al contului, nu doar OHLC public), fie (b) o promovare
graduală/monitorizată (shadow → gate, nu direct shadow → decizie unică),
urmând exact tiparul deja folosit pentru Kalman pe `tradeall.py` (shadow
17 iul → gate 19 iul → primar doar pe un simbol, niciodată un salt direct).

## Fișiere

- `verify_adaptive_dca.py` — testul pe pragul de DCA, refolosește motorul din
  `kraken/backtest_adaptive.py` (sare peste partea Chronos/ML).
- `verify_adaptive_reentry.py` — testul pe pragul de reintrare, cu bariera de
  reintrare adăugată (lipsă din uneltele originale).

Rulare (din rădăcina repo, cu `myenv` activat):
```bash
python3 research/kraken_adaptive_thresholds/verify_adaptive_dca.py
python3 research/kraken_adaptive_thresholds/verify_adaptive_reentry.py
```

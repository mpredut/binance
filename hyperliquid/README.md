# Hyperliquid — integrare, stare operațională și strategie HYPE

Directorul conține trei capabilități distincte: providerul HYPE/USDC spot folosit
de motorul comun `strategies/spot_dca`, motorul istoric PERP direcțional și motorul
delta-neutral. Faptul că există cod și configurație nu înseamnă că un proces este
activ în producție.

## Starea producției — 21 august 2026

- `hl_dca_bot.py` rulează manual, în afara `procs.conf`, cu `STRAT_EXECUTE` și
  `HL_LIVE_ORDERS` active; nu este supravegheat și nu repornește automat;
- la auditul din 21 august 2026 avea zero ordine deschise și era blocat de ordinul
  fictiv `PAPER-1` rămas dintr-o sesiune paper;
- `dn_bot.py` și watcherul sunt opriți și comentați în manifest;
- launcherul versionat separă acum starea HL de Kraken și PAPER de LIVE, dar
  procesul curent folosește codul vechi până la un restart controlat;
- `monitortrades` pentru HYPE/Hyperliquid rămâne dezactivat prin instrument gate.

Verificarea autoritativă este întotdeauna combinația dintre:

```bash
rg -n 'hl_dca|dn_bot' procs.conf
ps -ef | grep -E '[h]l_dca_bot|[d]n_bot'
python3 verify_tools/ownership_inventory.py --running
```

## Entry-pointuri

| Fișier | Piață | Motor | Stare |
|---|---|---|---|
| `hl_dca_bot.py` | HYPE/USDC spot | `strategies.spot_dca` (base v2) | rulează manual, REAL, blocat până la reconcilierea stării |
| `hl_bot.py` | PERP long/short | `hyperliquid/strategy.py` | legacy, neînregistrat/nepornit |
| `dn_bot.py` | spot long + perp short | `delta_neutral.py` | oprit explicit în manifest |
| `providers/hyperliquid_provider.py` | spot | contract `StrategyExecutor` | adaptor importat lazy |
| `hl_client.py` | spot și perp | SDK Hyperliquid | wrapper pentru citiri/ordine |

`hl_dca_bot.py` folosește același motor financiar live/replay ca botul Kraken;
providerul schimbă venue-ul, nu regulile strategiei.

## Configurație și precedență

Launcherul încarcă mai întâi `hyperliquid/.env`, apoi `hyperliquid/config.env`, iar
valorile deja definite nu sunt suprascrise. Prin urmare:

```text
.env local (runtime)  >  config.env versionat  >  valorile implicite din cod
```

`config.env` și override-urile locale descriu acum profilul long-term TP5. La
verificarea din 21 august 2026, parametrii nesensibili efectivi erau:

```text
entry 50 USDC | DCA 30 USDC la -2% | plafon 500 USDC | SL 7%
TP 5% | trend-hold activ | trailing adaptiv 1,5–8%
```

Nu deduce configurația live citind numai `config.env`. Pentru diagnostic, încarcă
fișierele în aceeași ordine ca launcherul și afișează numai cheile nesensibile.
Nu comite `.env` și nu afișa cheia agent-wallet.

## Porți de siguranță

Pentru `hl_dca_bot.py`, banii reali cer simultan:

1. proces lansat fără `--paper`;
2. `STRAT_EXECUTE=true`;
3. `HL_LIVE_ORDERS=true`;
4. cheie agent-wallet și cont valide;
5. ownership fără conflict cu alt proces pe același sold HYPE spot.

Lipsa oricărei porți păstrează PAPER sau face providerul să refuze ordinul. Aceste
porți nu înlocuiesc aprobarea operațională și includerea explicită în manifest.

## Fee spot și implicația pentru TP

Spot și PERP au grile diferite. La tier-ul spot de bază, documentația oficială
Hyperliquid indică aproximativ `0,040% maker` și `0,070% taker` per fill; valorile
vechi `0,015%/0,045%` sunt pentru PERP și nu justifică un TP spot de `0,5%`.
Tier-ul contului, staking-ul, builder fee-ul și tipul efectiv de fill pot schimba
costul. Backtestul trebuie să modeleze separat LIMIT/MARKET și un scenariu stress.

Sursă: <https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees>.

## Analiza long-term HYPE — 21 august 2026

Studiul offline a folosit datasetul înghețat HYPE/USDC spot Hyperliquid: 3.772
bare de 4h (~628 zile), walk-forward OOS cu ferestre de 15/30/60 zile, stare
resetată per TEST, fill-uri parțiale și ordine intrabar worst-case.

Scenariul central a folosit fee `0,04% LIMIT / 0,07% MARKET`; stress a folosit
`0,07% / 0,10%`, spread 20 bps, slippage market 30 bps și maximum 50% fill LIMIT
per bară. Ipotezele de execuție sunt conservatoare, dar încă necalibrate din
fill-uri reale Hyperliquid.

Randamentul mediu în stress:

| Variantă | 15 zile | 30 zile | 60 zile |
|---|---:|---:|---:|
| profil efectiv TP5 adaptiv | -0,114% | -0,114% | +0,393% |
| **TP3 + trend-hold + trail fix 3%** | **+0,098%** | **+0,464%** | **+1,069%** |
| TP5 + trail fix 3% | -0,002% | +0,018% | +0,526% |

Candidatul preferat pentru **shadow**, nu pentru live, este `long_tp3_trail3`:
aceleași sume și SL, TP armat la 3%, trend-hold activ și trailing fix 3%. A avut
DD stress mai mic decât profilul efectiv la toate
orizonturile (`5,74/8,78/8,82%` față de `6,41/9,49/10,09%`). Profilul agresiv
TP5/trail3/SL10 a avut media cea mai mare, dar DD stress a urcat la `16,7%` pe
60 de zile, deci nu este candidatul robust.

Niciun candidat nu a trecut gate-ul formal de promovare. `long_tp3_trail3` a
îmbunătățit media/tail/DD, dar nu a câștigat suficient de consistent fold-cu-fold
în schema de 15 zile. Concluzia este **shadow/paper only** până la dovezi forward,
nu modificare live.

Rularea de confirmare a fost executată exclusiv pe hostul DEV `backtest`.
Artefactul temporar curent este:

```text
/tmp/hl_dev_sweep_20260821.json
```

Artefactul nu este versionat; cifrele și ipotezele durabile sunt păstrate aici și în
`chatgpt_agent_work/OPEN_ACTIONS_PROD_FINANCIAL.md`.

## Co-mingling și ownership

Soldul HYPE spot este unic pe wallet. Dacă DN ar fi repornit, piciorul lui long
spot ar împărți soldul cu `hl_dca_bot` sau `monitortrades`; un SELL de „tot
available” ar putea desface hedge-ul. Înaintea oricărei activări, folosește un
subcont/wallet separat sau demonstrează ownership exclusiv. Faptul că DN este acum
oprit elimină conflictul runtime curent, nu riscul arhitectural la repornire.

## Autentificare

Hyperliquid folosește semnătură wallet EIP-712/ECDSA. Pentru automatizare se
folosește un agent/API wallet aprobat, nu cheia principală:

- `HL_SECRET_KEY` — cheia privată a agentului;
- `HL_ACCOUNT_ADDRESS` — adresa contului principal.

Secretele rămân exclusiv în `.env`, sunt excluse din Git și trebuie incluse în
procedura de backup/disaster recovery.

## Comenzi sigure

```bash
# import/provider, fără ordine (din rădăcina repo-ului)
cd /home/predut/binance
myenv/bin/python -m unittest -q tests.test_hyperliquid_provider_executor

# launcher forțat PAPER; nu îl adăuga în manifest doar pentru test
cd hyperliquid
../myenv/bin/python hl_dca_bot.py --paper
```

Pornirea PAPER este tot un proces persistent; oprește-l controlat după verificare.
Nu folosi comenzile de mai sus ca substitut pentru gate-ul de promovare.

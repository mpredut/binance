# Kraken bot — DCA + take-profit

Port al strategiei de la `121trade` (Trading 212) pe **Kraken (Spot)**.
Aceeași logică: intră la `market − x%`, mai cumpără pe scădere (DCA), vinde tot
la `preț_mediu × (1 + TP%)`, apoi reia ciclul. Simbolul e configurabil.

## Structura

| Fișier | Rol |
|--------|-----|
| `common.py` | utilitare: log, `.env`, HTTP (GET + POST form) |
| `kraken_client.py` | client REST Kraken: **public** (ticker, asset_pairs) + **privat** (balance, add_order, cancel_order, query_orders) cu semnătură HMAC-SHA512 |
| `market_data.py` | preț + disponibilitate pereche (detector de „listare") |
| `notify.py` | notificări ntfy + email (prin `alertnotifiers.py` din rădăcină) |
| `strategy.py` | motorul DCA + take-profit, cu P&L **net** (fee real Kraken) |
| `kraken_bot.py` | punct de intrare, config din `.env` |
| `.env.example` | șablon de configurare (copiază în `.env`) |

## Pornire

```bash
cp .env.example .env      # apoi pune KRAKEN_API_KEY / KRAKEN_API_SECRET
python3 kraken_bot.py --find-pair hype   # afla perechea exacta (public, fara chei)
python3 kraken_bot.py --price            # vezi pretul (public)
python3 kraken_bot.py --paper            # ruleaza strategia in PAPER (fara bani)
python3 kraken_bot.py                     # LIVE (cand STRAT_EXECUTE=true)
```

## Diferențe Kraken vs Trading 212 (de reținut)

| | Trading 212 | Kraken |
|---|---|---|
| Auth | Basic (key:secret) | **HMAC-SHA512** semnat (validat pe vectorul din docs) |
| Preț | Yahoo Finance | **Ticker public Kraken** |
| Cost mediu poziție | îl dă API-ul (`averagePrice`) | **NU** — îl urmărim noi din fill-uri |
| Status ordin executat | `/orders/{id}` dă **404** | `QueryOrders` **merge** și pt ordine închise |
| Fee | 0.15% conversie FX × 2 | **fee de tranzacționare** ~0.26% taker / ~0.16% maker (real, raportat de Kraken) |
| Sizing | valută cont (RON/EUR) → USD | direct în valuta de cotare a perechii |

## Stare disponibilitate simboluri (verificat azi)

- **HYPE** ✅ listat — perechi `HYPEEUR`, `HYPEUSD`. Gata de rulat.
- **SPCX** ❌ NU e pe Kraken (niciun token SpaceX / xStock). Pune `KRAKEN_PAIR=...` când/dacă apare;
  botul așteaptă singur până atunci (la fel ca SPCX pe T212).

## ⚠ Economie
Fee Kraken spot ~0.26% taker per tranzacție ⇒ **~0.5% pe round-trip**.
`STRAT_TAKEPROFIT_PCT` trebuie să fie peste acest prag (+ spread) ca să iasă profit net.
Ordinele **limit** care stau în carte sunt adesea *maker* (~0.16%), mai ieftine.

## Două simboluri simultan
Rulează două instanțe, fiecare cu perechea ei (state separat `.state_<PAIR>.json`):
```bash
python3 kraken_bot.py --pair HYPEEUR &
python3 kraken_bot.py --pair SPCXEUR &     # cand SPCX va exista
```

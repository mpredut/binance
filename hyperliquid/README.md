# Hyperliquid bot — DCA + take-profit (perp long-only)

Port al strategiei de la `121trade` / `kraken`, pe **Hyperliquid**.
Aceeași logică: intră long la `market − x%`, mai cumpără pe scădere (DCA),
vinde tot (reduce-only) la `preț_mediu × (1 + TP%)`, reia ciclul.

## ⚠ Rulează cu Python-ul din venv-ul cu SDK
SDK-ul oficial e instalat în `/home/mariusp/binance/.venv` (Python 3.14):
```bash
cd hyperliquid
/home/mariusp/binance/.venv/bin/python hl_bot.py --price       # public
/home/mariusp/binance/.venv/bin/python hl_bot.py --paper       # paper
# sau:  source ../.venv/bin/activate && python hl_bot.py
```

## Structura
| Fișier | Rol |
|--------|-----|
| `common.py` | log, `.env` |
| `hl_client.py` | wrapper peste SDK-ul Hyperliquid (Info=citiri, Exchange=ordine semnate) |
| `market_data.py` | preț (all_mids) + disponibilitate monedă |
| `notify.py` | ntfy + email (alertnotifiers) |
| `strategy.py` | motor DCA + take-profit, P&L net |
| `hl_bot.py` | entry point, config din `.env` |
| `.env.example` | șablon |

## Autentificare (diferită de T212/Kraken)
Hyperliquid NU folosește key/secret, ci **semnătură de wallet (EIP-712/ECDSA)**.
Folosește un **agent / API wallet** (NU cheia principală):
1. Pe Hyperliquid: **More → API → Generate** agent wallet → **Approve**.
2. În `.env`:
   - `HL_SECRET_KEY` = cheia privată a agentului (poate tranzacționa, NU retrage)
   - `HL_ACCOUNT_ADDRESS` = adresa contului principal (cu USDC)

## De ce PERP long-only
- HYPE perp e cel mai **lichid** pe Hyperliquid (spot-ul folosește notație `@index`).
- La **levier 1x long-only**, e cvasi-spot: lichidarea e foarte departe (preț → ~0).
- `clearinghouseState` dă direct **mărimea poziției + prețul mediu** → reconciliere curată.
- Risc suplimentar față de spot: **funding** (de obicei mic) și, teoretic, lichidare la levier mare → ține `HL_LEVERAGE=1`.

## ✅ Avantaj: fee minuscul
Fee Hyperliquid ~**0.045% taker / 0.015% maker** (vs 0.5% Kraken, 0.30% T212).
Deci `STRAT_TAKEPROFIT_PCT` poate fi **strâns** (0.3–0.5%) și tot iese profit net.

## Diferențe față de Kraken/T212
| | T212 | Kraken | Hyperliquid |
|---|---|---|---|
| Auth | Basic | HMAC | **semnătură wallet (SDK)** |
| Preț | Yahoo | Ticker | `all_mids` |
| Poziție + avg | da (API) | nu (urmărim noi) | **da** (`entryPx`) |
| Fee | 0.30% FX | ~0.5% rt | **~0.03–0.09% rt** |
| Produs | acțiuni | spot crypto | **perp** (long-only aici) |

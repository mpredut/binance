# HYPE dataset înghețat (proxy Hyperliquid)

Dataset reproductibil pentru re-rularea INSTANT a oricărui candidat de strategie Kraken/HYPE,
fără dependență de fetch live (Kraken public OHLC dă doar ~120 zile; aici avem ~628).

- `HYPEUSDC_240m_hlspot.csv` — 3772 bare de 4h (~628 zile)
- `HYPEUSDC_1440m_hlspot.csv` — 628 bare de 1 zi
- `manifest.json` — sursă, hash sha256, dată fetch

**Sursă:** Hyperliquid public OHLC (`api.hyperliquid.xyz`), HYPE/USDC spot, via
`offline/runners/fetch_hyperliquid_candles.py`. **PROXY cross-venue** pentru mișcarea prețului
HYPE — NU execuția Kraken (fill-urile reale diferă). Bun pentru robustețe, nu pentru absolut.

## Verificare candidați pe acest dataset (31 ferestre OOS, warmup 40 bare, fee 0.26%/leg)

BASE v2: medie **−0.70%**, cea mai slabă fereastră −15.21%, DD max 16.0%.

| Candidat | Δ medie vs base | W/T/L | worst fold | DD max | verdict |
|---|---|---|---|---|---|
| **overlay orig** (topup 2000/trail 5) | −0.94pp | 11/2/18 | **−25.74%** | **27.6%** | RESPINS — tail risk + instabilitate |
| **A** trailing-adaptiv | −0.03pp | 9/16/6 | −15.21% | 16.0% | wash — semn Δ instabil între config-uri; nu adaugă tail. Shadow-only |
| **B** frână-DCA-downtrend | +1.09pp* | 9/14/8 | **−8.85%** | 11.3% | OFF, dar reduce TAIL/DD real (worst −8.85 vs −15.21). Merită shadow țintit pe tail |
| **tp4** (TP 5→4) | −0.00pp | 5/22/4 | −15.21% | 16.0% | marginal — aproape mereu identic |
| **dca15** (DCA 1.25→1.5) | +0.17pp | 6/21/4 | −15.21% | 16.0% | modest — mostly ties |

\* Semnul Δ mediu la A și B diferă de raportul Codex (A +0.43pp / B −0.56pp la ei) — divergența
vine din configul ferestrelor walk-forward. **Faptul că semnul se răstoarnă la schimbarea ferestrei
= dovada că niciunul nu are un avantaj robust de randament.** Ce e consistent: overlay-ul are tail
risk clar (worst −25.74%, DD 27.6%), iar B reduce tail-ul (worst −8.85%).

## Concluzie (aliniată cu revalidarea Codex 19 aug)
- **Overlay: respins** — motivul exact e **instabilitatea + riscul de coadă**, NU pierdere uniformă
  la medie (câștigă 11/31 ferestre în trend-uri puternice, dar tail-ul îl scufundă).
- **A / tp4 / dca15:** marginale, semn instabil → doar shadow, fără promovare.
- **B:** OFF pe randament, dar unghiul de **protecție tail/DD** e real și subexplorat.
- **Live neschimbat.** Prag de promovare: min 30 zile + 20 evenimente de divergență în shadow.

# Trading212 — dataseturi înghețate și baseline live

Data: 2026-08-20

CSV-urile sunt copii canonice Yahoo, fără ultima coadă de lumânări încă în
formare. Hash-urile și intervalele sunt în `manifest.json`. Configurația fiecărui
profil este citită direct din `212trading/config.<profile>.env`; runnerul folosește
același `Strategy.step()` ca live și nu contactează API-ul de ordine T212.

## Rezultate

| Profil | Date | Central mean/worst/DD | Stress mean/worst/DD | Cicluri central | Verdict |
|---|---|---:|---:|---:|---|
| NVDA | 501 bare 1d, 2 ani | +0,536% / +0,450% / 0,914% | +0,458% / +0,360% / 1,037% | 9 | baseline pozitiv, numai 3 fold-uri |
| RGNT | 176 bare 1d, de la listare | -4,771% / -13,778% / 18,579% | -4,771% / -13,778% / 18,579% | 1 | configurația cere reevaluare; dovadă foarte rară |
| SPCX | 1794 bare 5m, 1 lună | +0,651% / -1,689% / 1,763% | +0,504% / -1,689% / 1,763% | 1 | caracterizare, istoric intraday prea scurt |

Central: spread 10bps, slippage MARKET 15bps, maximum 75% fill LIMIT/bară,
intrabar worst-case. Stress: 20bps, 30bps, maximum 50%, worst-case.

Toate profilele declară `STRAT_CURRENCY=USD`: bugetele și cantitățile sunt
exprimate în USD. Endpointul T212 `/equity/account/info` a confirmat read-only
`currencyCode=RON`, deci `STRAT_FX_FEE_PCT=0.15` rămâne corect și este inclus în
baseline. Nu este necesară o serie FX istorică pentru dimensionarea unui buget
fixat în USD; ea ar deveni necesară dacă bugetele strategiei ar fi exprimate în
RON/EUR.

## Reproducere

```bash
.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile nvda --interval 1d \
  --dataset offline/research/t212_dataset/nvda/datasets/NVDA_1d_9e8cbd3c6ff5.csv \
  --spread-bps 10 --market-slippage-bps 15 \
  --partial-fill-ratio 0.75 --intrabar-policy worst_case

.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile rgnt --interval 1d \
  --dataset offline/research/t212_dataset/rgnt/datasets/RGNT_1d_b67a2932ddd6.csv \
  --spread-bps 10 --market-slippage-bps 15 \
  --partial-fill-ratio 0.75 --intrabar-policy worst_case

.venv/bin/python offline/runners/t212_walk_forward_baseline.py \
  --profile spcx --interval 5m \
  --dataset offline/research/t212_dataset/spcx/datasets/SPCX_5m_ada3bbb43c6d.csv \
  --spread-bps 10 --market-slippage-bps 15 \
  --partial-fill-ratio 0.75 --intrabar-policy worst_case
```

Pentru stress se schimbă la `--spread-bps 20 --market-slippage-bps 30
--partial-fill-ratio 0.50`; datasetul și celelalte opțiuni rămân identice.

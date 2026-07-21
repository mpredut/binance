#!/usr/bin/env python3
"""
vol_chronos.py — test onest: poate un model de fundatie zero-shot (Chronos, Amazon)
sa prezica NIVELUL VIITOR de volatilitate mai bine decat persistenta simpla?

De ce volatilitate si nu pret: clustering-ul de volatilitate (perioadele calme/agitate
se succed in blocuri, efect GARCH) e un fenomen mult mai robust decat directia
pretului (aproape random walk pe orizonturi lichide — vezi forecast.py: pe BTC modelele
antrenate nu bat baseline-ul acolo). Chronos e ZERO-SHOT (nu se antreneaza pe datele
tale — doar inference), deci riscul de overfitting e mult mai mic decat la boosting-ul
din forecast.py.

Serie tinta: volatilitate realizata TRAILING pe fereastra de --win ore (std log-returns),
calculata la fiecare ora. Intrebare: dat fiind istoricul acestei serii pana la ora i,
cat va fi ea peste --horizon ore? Comparat cu baseline "ramane la fel" (persistenta —
acelasi baseline "lindy" folosit si in forecast.py).

Daca bate baseline-ul onest, urmatorul pas ar fi inlocuirea/completarea lui vol_1h_pct
din shadow_signals.py cu predictia asta (pragurile adaptive K_REENTRY/K_DCA ar folosi
volatilitatea VIITOARE estimata, nu doar cea trecuta).

Rulare:
  python3 vol_chronos.py --symbol TAOUSDC --days 400 --horizon 24 --win 24 --eval
  python3 vol_chronos.py --symbol BTCUSDC --days 400 --horizon 24 --win 24 --eval
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # forecast/ -> radacina repo
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_survival import fetch_klines  # noqa: E402

MODEL_NAME = "amazon/chronos-t5-tiny"   # 8M param, cel mai mic — CPU-friendly
MAX_CONTEXT = 512                       # ore de istoric date modelului per predictie (plafon)
BATCH = 8                               # cate ferestre de test batch-uim intr-un apel
                                         # (masina are doar 3.8GB RAM si ruleaza boti live —
                                         # batch mare (32) a dus la swap si kill; 8 e sigur)


def realized_vol_series(px: np.ndarray, win: int) -> np.ndarray:
    """rv[i] = std al log-returns pe fereastra TRAILING [i-win, i]. NaN unde nu-i istoric."""
    logp = np.log(px)
    r = np.diff(logp)
    rv = np.full(len(px), np.nan)
    for i in range(win, len(px)):
        rv[i] = np.std(r[i - win:i])
    return rv


def _load_pipeline():
    from chronos import ChronosPipeline
    print(f"  incarc {MODEL_NAME} (zero-shot, fara antrenare)...")
    return ChronosPipeline.from_pretrained(MODEL_NAME, device_map="cpu", torch_dtype=torch.float32)


def walk_forward_vol(rv: np.ndarray, horizon: int, warmup: int, stride: int):
    """Pt fiecare ora de test i (cu pas stride, ca sa fie fezabil pe CPU): da modelului
    rv[:i+1] (plafonat la MAX_CONTEXT) si cere predictia la +horizon ore. Compara cu
    baseline (persistenta: rv[i]) si cu adevarul (rv[i+horizon])."""
    pipe = _load_pipeline()
    n = len(rv)
    idxs = list(range(warmup, n - horizon, stride))
    preds, bases, actuals = [], [], []
    t0 = time.time()
    for b in range(0, len(idxs), BATCH):
        chunk = idxs[b:b + BATCH]
        contexts = [torch.tensor(rv[max(0, i + 1 - MAX_CONTEXT):i + 1], dtype=torch.float32)
                    for i in chunk]
        # prediction_length = horizon; luam MEDIANA peste sample-uri si peste orizont
        # (ne intereseaza nivelul de volatilitate PE FEREASTRA viitoare, nu un punct exact)
        forecast = pipe.predict(contexts, prediction_length=horizon)
        for k, i in enumerate(chunk):
            path = forecast[k].numpy()                 # [num_samples, horizon]
            point = float(np.median(np.median(path, axis=0)))
            preds.append(point)
            bases.append(float(rv[i]))
            actuals.append(float(rv[i + horizon]))
        done = b + len(chunk)
        print(f"    {done}/{len(idxs)} ferestre testate ({time.time()-t0:.0f}s)", end="\r")
    print()
    preds, bases, actuals = map(np.array, (preds, bases, actuals))
    mae_model = float(np.mean(np.abs(preds - actuals)))
    mae_base = float(np.mean(np.abs(bases - actuals)))
    # acuratete de DIRECTIE: creste/scade volatilitatea fata de nivelul curent?
    dir_actual = (actuals - bases) > 0
    dir_pred = (preds - bases) > 0
    dir_acc = float(np.mean(dir_actual == dir_pred))
    corr = float(np.corrcoef(preds, actuals)[0, 1]) if len(preds) > 2 else float("nan")
    return {
        "n_test": len(preds),
        "mae_model": mae_model,
        "mae_baseline_persistenta": mae_base,
        "improvement_pct": round(100 * (1 - mae_model / mae_base), 1) if mae_base else None,
        "directie_acc": round(dir_acc, 3),
        "corr_model_vs_actual": round(corr, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Test onest Chronos zero-shot pt predictie volatilitate.")
    ap.add_argument("--symbol", default="TAOUSDC")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--win", type=int, default=24, help="fereastra (ore) pt volatilitatea realizata")
    ap.add_argument("--horizon", type=int, default=24, help="cu cate ore inainte prezicem")
    ap.add_argument("--stride", type=int, default=6, help="pas intre ferestrele de test (ore) — CPU")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()

    ts, px = fetch_klines(args.symbol, args.days)
    print(f"[{args.symbol}] {len(px)} lumanari 1h")
    rv = realized_vol_series(px, args.win)
    warmup = args.win + MAX_CONTEXT // 4     # putin istoric minim inainte sa incepem testul
    if args.eval:
        rep = walk_forward_vol(rv, args.horizon, warmup, args.stride)
        print(f"  test pe {rep['n_test']} ferestre (stride={args.stride}h), "
              f"orizont={args.horizon}h, fereastra_vol={args.win}h")
        print(f"    MAE model={rep['mae_model']:.5f}  baseline(persistenta)={rep['mae_baseline_persistenta']:.5f}"
              f"  -> {rep['improvement_pct']}% {'mai bun' if (rep['improvement_pct'] or 0) > 0 else 'mai slab'}")
        print(f"    acuratete directie (creste/scade vol): {rep['directie_acc']}")
        print(f"    corelatie model vs adevar: {rep['corr_model_vs_actual']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

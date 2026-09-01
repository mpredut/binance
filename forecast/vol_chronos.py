#!/usr/bin/env python3
"""
vol_chronos.py — honest test of whether Amazon's zero-shot Chronos foundation model
predicts the FUTURE volatility LEVEL better than simple persistence.

Why volatility instead of price: volatility clustering, where calm and turbulent periods
occur in blocks as in GARCH, is more robust than price direction, which is close to a
random walk on liquid horizons. In forecast.py, trained BTC models do not beat that
baseline. Chronos is ZERO-SHOT inference without training on this data, reducing
overfitting risk relative to forecast.py boosting.

Target series: TRAILING realized volatility over --win hours, as standard deviation of
log returns calculated hourly. Given its history through hour i, predict its value after
--horizon hours and compare with the unchanged-value persistence baseline, matching the
Lindy baseline used in forecast.py.

If it honestly beats baseline, a later step could replace or supplement shadow_signals.py
vol_1h_pct so adaptive K_REENTRY/K_DCA thresholds use estimated FUTURE volatility rather
than only historical volatility.

Run:
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # forecast/ -> repository root
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_survival import fetch_klines  # noqa: E402

MODEL_NAME = "amazon/chronos-t5-tiny"   # smallest 8M-parameter model; CPU-friendly
MAX_CONTEXT = 512                       # maximum history hours supplied per prediction
BATCH = 8                               # test windows batched per call
                                         # The 3.8GB machine also runs live bots; batch 32
                                         # caused swapping and termination, while 8 is safe.


def realized_vol_series(px: np.ndarray, win: int) -> np.ndarray:
    """Return trailing [i-win, i] log-return standard deviation, with NaN before history."""
    logp = np.log(px)
    r = np.diff(logp)
    rv = np.full(len(px), np.nan)
    for i in range(win, len(px)):
        rv[i] = np.std(r[i - win:i])
    return rv


def _load_pipeline():
    from chronos import ChronosPipeline
    print(f"  loading {MODEL_NAME} (zero-shot, no training)...")
    return ChronosPipeline.from_pretrained(MODEL_NAME, device_map="cpu", torch_dtype=torch.float32)


def walk_forward_vol(rv: np.ndarray, horizon: int, warmup: int, stride: int):
    """For each test hour i sampled by stride, give the model rv[:i+1] capped at
    MAX_CONTEXT and request a +horizon-hour prediction. Compare against persistence
    rv[i] and actual rv[i+horizon]."""
    pipe = _load_pipeline()
    n = len(rv)
    idxs = list(range(warmup, n - horizon, stride))
    preds, bases, actuals = [], [], []
    t0 = time.time()
    for b in range(0, len(idxs), BATCH):
        chunk = idxs[b:b + BATCH]
        contexts = [torch.tensor(rv[max(0, i + 1 - MAX_CONTEXT):i + 1], dtype=torch.float32)
                    for i in chunk]
        # prediction_length=horizon; take the median across samples and horizon because
        # the future-window volatility level matters, not one exact point.
        forecast = pipe.predict(contexts, prediction_length=horizon)
        for k, i in enumerate(chunk):
            path = forecast[k].numpy()                 # [num_samples, horizon]
            point = float(np.median(np.median(path, axis=0)))
            preds.append(point)
            bases.append(float(rv[i]))
            actuals.append(float(rv[i + horizon]))
        done = b + len(chunk)
        print(f"    {done}/{len(idxs)} windows tested ({time.time()-t0:.0f}s)", end="\r")
    print()
    preds, bases, actuals = map(np.array, (preds, bases, actuals))
    mae_model = float(np.mean(np.abs(preds - actuals)))
    mae_base = float(np.mean(np.abs(bases - actuals)))
    # DIRECTION accuracy: does volatility rise or fall from the current level?
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
    ap.add_argument("--win", type=int, default=24, help="the window (hours) for the realised volatility")
    ap.add_argument("--horizon", type=int, default=24, help="how many hours ahead we predict")
    ap.add_argument("--stride", type=int, default=6, help="the step between test windows (hours) — CPU")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()

    ts, px = fetch_klines(args.symbol, args.days)
    print(f"[{args.symbol}] {len(px)} lumanari 1h")
    rv = realized_vol_series(px, args.win)
    warmup = args.win + MAX_CONTEXT // 4     # minimum history before testing begins
    if args.eval:
        rep = walk_forward_vol(rv, args.horizon, warmup, args.stride)
        print(f"  tested on {rep['n_test']} windows (stride={args.stride}h), "
              f"orizont={args.horizon}h, fereastra_vol={args.win}h")
        print(f"    MAE model={rep['mae_model']:.5f}  baseline(persistenta)={rep['mae_baseline_persistenta']:.5f}"
              f"  -> {rep['improvement_pct']}% {'mai bun' if (rep['improvement_pct'] or 0) > 0 else 'mai slab'}")
        print(f"    acuratete directie (creste/scade vol): {rep['directie_acc']}")
        print(f"    corelatie model vs adevar: {rep['corr_model_vs_actual']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

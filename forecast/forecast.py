#!/usr/bin/env python3
"""
forecast.py — NEW PARALLEL module for experimental FUTURE trend and price estimation.

Runs ALONGSIDE existing analysis without trading or replacing it. Produces forecast.json
and an honest walk-forward report measured on unseen data against the Lindy baseline
that assumes the current trend persists.

Models, all available without new dependencies because sklearn is already installed:
  * lindy: baseline that the latest 24-hour sign persists
  * logit: logistic regression over scaled features
  * boost: HistGradientBoosting, which generally beats LSTM at this data volume without GPU
priceprediction.py contains a Keras LSTM, but TensorFlow is not installed. Boosting is the
correct starting point; add LSTM only if it later beats boosting walk-forward.

One-hour candle features include multi-horizon returns, volatility, Mann-Kendall Z
for trend strength, Hurst regime, RSI, and signal-to-noise. Targets are direction and
magnitude over the next 24 hours.

  python3 forecast.py --symbol TAOUSDC --days 400 --eval        # honest report
  python3 forecast.py --symbol TAOUSDC --forecast               # -> forecast.json
  python3 forecast.py --symbol TAOUSDC --forecast --loop 60     # every hour

forecast.json is compatible with the Hyperliquid bot's signal.json format
(trend/confidence/ts), so the same file can feed SIGNAL_SOURCE=file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # forecast/ -> repository root
sys.path.insert(0, _ROOT)
from trend_survival import fetch_klines  # noqa: E402
from trend_stats import mann_kendall, hurst_rs  # noqa: E402

HORIZON_H = 24
WARMUP = 240            # history hours required for features; Hurst uses 240h

FEATURES = ["r1", "r4", "r8", "r24", "r72", "vol24", "vol72", "mk_z", "hurst", "rsi", "snr24"]


def _feat_row(i: int, logp: np.ndarray, px: np.ndarray) -> list[float]:
    r1 = logp[i] - logp[i - 1]
    r4 = logp[i] - logp[i - 4]
    r8 = logp[i] - logp[i - 8]
    r24 = logp[i] - logp[i - 24]
    r72 = logp[i] - logp[i - 72]
    vol24 = float(np.std(np.diff(logp[i - 24:i + 1])))
    vol72 = float(np.std(np.diff(logp[i - 72:i + 1])))
    _, mk_z, _ = mann_kendall(px[i - 24:i + 1])
    h = hurst_rs(px[i - WARMUP:i + 1]) or 0.5
    d = np.diff(px[i - 14:i + 1])
    up, dn = d[d > 0].sum(), -d[d < 0].sum()
    rsi = 100.0 * up / (up + dn) if up + dn > 0 else 50.0
    snr24 = r24 / (vol24 * np.sqrt(24) + 1e-12)        # signal contribution relative to noise
    return [r1, r4, r8, r24, r72, vol24, vol72, mk_z, h, rsi, snr24]


def build_dataset(px: np.ndarray):
    logp = np.log(px)
    X, y_dir, y_mag = [], [], []
    for i in range(WARMUP, len(px) - HORIZON_H):
        X.append(_feat_row(i, logp, px))
        fut = logp[i + HORIZON_H] - logp[i]
        y_dir.append(1 if fut > 0 else 0)
        y_mag.append(fut)
    return np.array(X), np.array(y_dir), np.array(y_mag)


def walk_forward(X, y_dir, y_mag, train_frac=0.7, refit_every=168):
    """Train on the past, predict the UNSEEN future, and refit weekly."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    n = len(X)
    start = int(n * train_frac)
    acc = {"lindy": [], "logit": [], "boost": []}
    mag_err, mag_base = [], []
    i = start
    while i < n:
        j = min(i + refit_every, n)
        clf = HistGradientBoostingClassifier(max_iter=200, random_state=0).fit(X[:i], y_dir[:i])
        reg = HistGradientBoostingRegressor(max_iter=200, random_state=0).fit(X[:i], y_mag[:i])
        logit = make_pipeline(StandardScaler(),
                              LogisticRegression(max_iter=1000)).fit(X[:i], y_dir[:i])
        sl = slice(i, j)
        acc["lindy"] += list((X[sl, 3] > 0).astype(int) == y_dir[sl])
        acc["logit"] += list(logit.predict(X[sl]) == y_dir[sl])
        acc["boost"] += list(clf.predict(X[sl]) == y_dir[sl])
        pm = reg.predict(X[sl])
        mag_err += list(np.abs(pm - y_mag[sl]))
        mag_base += list(np.abs(y_mag[sl]))             # baseline predicts zero movement
        i = j
    return {m: float(np.mean(v)) for m, v in acc.items()} | {
        "n_test": len(acc["boost"]),
        "mae_move_pct": float(np.mean(mag_err) * 100),
        "mae_baseline_pct": float(np.mean(mag_base) * 100),
    }


def live_forecast(px: np.ndarray, X, y_dir, y_mag, rep: dict, symbol: str) -> dict:
    """Train on ALL history and forecast from the latest candle.
    Use the model that WON walk-forward rather than assuming the more sophisticated
    model is better; logit generally beats boosting on the current data."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    use_boost = bool(rep) and rep["boost"] >= rep["logit"]
    if use_boost:
        clf = HistGradientBoostingClassifier(max_iter=200, random_state=0).fit(X, y_dir)
    else:
        clf = make_pipeline(StandardScaler(),
                            LogisticRegression(max_iter=1000)).fit(X, y_dir)
    reg = HistGradientBoostingRegressor(max_iter=200, random_state=0).fit(X, y_mag)
    logp = np.log(px)
    row = np.array([_feat_row(len(px) - 1, logp, px)])
    proba_up = float(clf.predict_proba(row)[0][1])
    move = float(reg.predict(row)[0])
    best_acc = max(rep["boost"], rep["logit"]) if rep else None
    return {
        # Compatible with the HL bot's signal.json: trend / confidence / ts.
        "trend": "up" if proba_up >= 0.5 else "down",
        "confidence": round(abs(proba_up - 0.5) * 2, 2),
        "ts": time.time(),
        # Additional evaluation and transparency fields.
        "symbol": symbol, "horizon_h": HORIZON_H,
        "proba_up": round(proba_up, 3),
        "expected_move_pct": round(move * 100, 2),   # Caution: MAE exceeds baseline; indicative only
        "model": "boost" if use_boost else "logit",
        "walkforward_accuracy": round(best_acc, 3) if best_acc else None,
        "baseline_accuracy": round(rep["lindy"], 3) if rep else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimare paralela trend+pret viitor (test).")
    ap.add_argument("--symbol", default="TAOUSDC")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--eval", action="store_true", help="doar raportul walk-forward")
    ap.add_argument("--forecast", action="store_true", help="scrie forecast.json")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "forecast.json"))
    ap.add_argument("--loop", type=float, default=0, help="minute intre prognoze (0 = o data)")
    args = ap.parse_args()

    while True:
        ts, px = fetch_klines(args.symbol, args.days)
        if len(px) < WARMUP + HORIZON_H + 100:
            print(f"! istoric insuficient ({len(px)} lumanari)"); return 1
        X, y_dir, y_mag = build_dataset(px)
        print(f"[{args.symbol}] {len(px)} lumanari 1h -> {len(X)} esantioane, orizont {HORIZON_H}h")
        rep = walk_forward(X, y_dir, y_mag)
        print(f"  acuratete directie pe {rep['n_test']} ore NEVAZUTE:")
        print(f"    lindy (persista): {rep['lindy']:.3f}   logit: {rep['logit']:.3f}   boost: {rep['boost']:.3f}")
        print(f"  amplitudine 24h:  MAE model {rep['mae_move_pct']:.2f}%  vs baseline(0) {rep['mae_baseline_pct']:.2f}%")
        if args.forecast:
            out = live_forecast(px, X, y_dir, y_mag, rep, args.symbol)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            print(f"  -> {args.out}: trend={out['trend']} conf={out['confidence']} "
                  f"miscare estimata {out['expected_move_pct']:+.2f}% / {HORIZON_H}h")
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    sys.exit(main())

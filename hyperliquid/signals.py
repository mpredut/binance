#!/usr/bin/env python3
"""
signals.py — strat de SEMNAL decuplat de strategie.

Strategia citeste un semnal: {"trend": "up"|"down"|"neutral", "confidence": 0..1}.
Sursa se alege cu SIGNAL_SOURCE:
  * off     -> mereu neutral (strategia ignora trendul)
  * builtin -> trend simplu din lumanari HL (media rapida vs media lenta)
  * file    -> citeste dintr-un JSON pe care MODELUL TAU il scrie (LSTM/price-analysis)

Format fisier extern (SIGNAL_FILE, implicit signal.json):
  {"trend": "down", "confidence": 0.72, "ts": 1781000000}
  - 'ts' = timestamp unix (secunde); daca semnalul e mai vechi de SIGNAL_MAX_AGE_MIN
    e considerat expirat -> neutral (ca sa nu tranzactionezi pe predictie veche).

Asa, cand faci LSTM-ul: doar scrii signal.json la fiecare predictie, botul il consuma.
REGULA: backtesteaza orice semnal inainte sa-i dai bani reali.
"""

from __future__ import annotations

import json
import os
import statistics
import time

from common import log, float_env

NEUTRAL = {"trend": "neutral", "confidence": 0.0, "source": "neutral"}


def _builtin_trend(client, coin: str) -> dict:
    fast = int(float_env("SIGNAL_FAST_H") or 12)
    slow = int(float_env("SIGNAL_SLOW_H") or 48)
    band = (float_env("SIGNAL_BAND_PCT") or 0.3) / 100
    candles = client.candles(coin, "1h", lookback_hours=slow + 6)
    closes = [float(c["c"]) for c in candles if "c" in c]
    if len(closes) < slow:
        return {**NEUTRAL, "source": "builtin(insuf. date)"}
    mf = statistics.mean(closes[-fast:])
    ms = statistics.mean(closes[-slow:])
    diff = (mf - ms) / ms if ms else 0.0
    trend = "up" if diff > band else "down" if diff < -band else "neutral"
    conf = round(min(1.0, abs(diff) / (band * 3)), 2) if band > 0 else 0.0
    return {"trend": trend, "confidence": conf, "source": "builtin",
            "detail": f"medie{fast}h={mf:.3f} medie{slow}h={ms:.3f} diff={diff*100:+.2f}%"}


def _file_signal(path: str) -> dict:
    if not os.path.exists(path):
        return {**NEUTRAL, "source": "file(lipsa)"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        log(f"  ! semnal fisier invalid: {e}")
        return {**NEUTRAL, "source": "file(corupt)"}
    ts = d.get("ts") or d.get("timestamp")
    max_age = (float_env("SIGNAL_MAX_AGE_MIN") or 60) * 60
    if ts:
        try:
            if time.time() - float(ts) > max_age:
                return {**NEUTRAL, "source": "file(expirat)"}
        except (TypeError, ValueError):
            pass
    tr = str(d.get("trend", "neutral")).lower()
    if tr not in ("up", "down", "neutral"):
        tr = "neutral"
    try:
        conf = float(d.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {"trend": tr, "confidence": conf, "source": "file"}


def get_signal(client, coin: str) -> dict:
    src = os.environ.get("SIGNAL_SOURCE", "off").strip().lower()
    if src == "builtin":
        return _builtin_trend(client, coin)
    if src == "analysis":               # analiza ta portata (WMA/panta pe ferestre de timp)
        from price_analysis import signal as analysis_signal
        return analysis_signal(client, coin)
    if src == "file":
        return _file_signal(os.environ.get("SIGNAL_FILE", "signal.json"))
    return {**NEUTRAL, "source": "off"}

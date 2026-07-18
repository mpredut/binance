#!/usr/bin/env python3
"""
shadow_signals.py — semnale SHADOW (strict observationale) rulate in paralel
cu modelul live din tradeall.py. NU iau decizii, NU ating place_order_smart —
doar publica chei suplimentare in snapshot + jurnalizeaza tranzitiile proprii,
ca sa poata fi comparate (vizual in tradeall_monitor.py, cantitativ in
tradeall_backtest.py) cu modelul actual INAINTE de orice promovare.

Componente:
  KalmanTrend  — filtru Kalman constant-velocity (stare: nivel + viteza).
                 Output: viteza trendului in %/min + incertitudinea ei +
                 directie {-1,0,+1} doar cand |vel| > 1.64*std (~90% incredere).
  vol_1h_pct   — volatilitate estimata la orizont 1h din fereastra BIG
                 existenta (log-returns, scalare sqrt-timp).
  ShadowJournal— writer pipe-text (acelasi tipar/sanitizare ca log_decision),
                 un rand DOAR la tranzitie de trend Kalman (condensat).

Config optional prin env (default-uri sanatoase in cod):
  SHADOW_KALMAN_QR   raport zgomot proces/masurare (default 0.05)
  SHADOW_K_REENTRY   k pentru prag adaptiv reintrare = k * vol_1h (default 2.0)
  SHADOW_K_DCA       k pentru prag adaptiv DCA       = k * vol_1h (default 1.0)
"""
from __future__ import annotations

import math
import os
from datetime import date

import numpy as np


def _f_env(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except ValueError:
        return default


KALMAN_QR = _f_env("SHADOW_KALMAN_QR", 0.0005)   # sweep 17 iul: 94% stabil dupa detectie, latenta ~15s
K_REENTRY = _f_env("SHADOW_K_REENTRY", 2.0)
K_DCA = _f_env("SHADOW_K_DCA", 1.0)

CONF_MULT = 1.64          # |vel| > 1.64*std -> directie cu ~90% incredere
MIN_VEL_PCT_MIN = 0.005   # sub 0.005%/min consideram plat indiferent de std
DT_MIN, DT_MAX = 0.05, 900.0


class KalmanTrend:
    """Filtru Kalman 1D constant-velocity pentru UN simbol.

    Stare x=[nivel, viteza(pret/sec)]; observatie = pretul. R (zgomotul de
    masurare) vine din epsilon-ul deja calculat de PriceWindow (unitati
    absolute de pret); Q = KALMAN_QR * R, discretizat cu dt real."""

    def __init__(self, qr: float = KALMAN_QR):
        self.qr = qr
        self.x = None          # [nivel, viteza]
        self.P = None          # covarianta starii
        self.last_ts = None
        self.trend = 0         # -1 / 0 / +1 (ultima directie confirmata)

    def update(self, ts: float, price: float, epsilon: float | None) -> dict:
        """Un pas predict+update. Returneaza dict cu vel (%/min), vel_std,
        trend si old_trend (pt detectarea tranzitiei de catre apelant)."""
        eps = float(epsilon) if epsilon else 0.0
        if eps <= 0:
            eps = max(price * 1e-4, 1e-9)   # warm-up: zgomot presupus 0.01% din pret
        R = eps * eps

        if self.x is None:
            self.x = np.array([price, 0.0])
            self.P = np.diag([R * 10.0, (price * 1e-3) ** 2])
            self.last_ts = ts
            return self._out(price, old_trend=self.trend)

        dt = min(max(ts - self.last_ts, DT_MIN), DT_MAX)
        self.last_ts = ts

        F = np.array([[1.0, dt], [0.0, 1.0]])
        q = self.qr * R
        Q = q * np.array([[dt ** 3 / 3.0, dt ** 2 / 2.0],
                          [dt ** 2 / 2.0, dt]])
        # predict
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        # update (H = [1, 0])
        y = price - self.x[0]
        S = self.P[0, 0] + R
        K = self.P[:, 0] / S
        self.x = self.x + K * y
        self.P = self.P - np.outer(K, self.P[0, :])

        old_trend = self.trend
        out = self._out(price, old_trend=old_trend)
        self.trend = out["trend"]
        return out

    def _out(self, price: float, old_trend: int) -> dict:
        vel = float(self.x[1])
        vel_std = math.sqrt(max(float(self.P[1, 1]), 0.0))
        vel_pct_min = vel / price * 100.0 * 60.0
        std_pct_min = vel_std / price * 100.0 * 60.0
        if abs(vel_pct_min) > max(CONF_MULT * std_pct_min, MIN_VEL_PCT_MIN):
            trend = 1 if vel_pct_min > 0 else -1
        else:
            trend = 0
        return {"vel": round(vel_pct_min, 5), "vel_std": round(std_pct_min, 5),
                "trend": trend, "old_trend": old_trend}


def vol_1h_pct(prices, sample_rate_sec: float) -> float | None:
    """Volatilitate (1 sigma) estimata pe orizont de 1h, in %, din fereastra
    de preturi existenta (log-returns, scalare sqrt-timp). None in warm-up."""
    p = np.asarray(prices, dtype=float)
    if len(p) < 20 or sample_rate_sec <= 0:
        return None
    p = p[p > 0]
    if len(p) < 20:
        return None
    rets = np.diff(np.log(p))
    std = float(np.std(rets))
    if std == 0.0:
        return 0.0
    return round(std * math.sqrt(3600.0 / sample_rate_sec) * 100.0, 4)


def adaptive_thresholds(vol1h: float | None) -> tuple[float | None, float | None]:
    """(adapt_reentry_pct, adapt_dca_pct) = k * vol_1h; None in warm-up."""
    if vol1h is None:
        return None, None
    return round(K_REENTRY * vol1h, 3), round(K_DCA * vol1h, 3)


class ShadowJournal:
    """Jurnal pipe-text pentru tranzitiile semnalelor shadow. Acelasi tipar ca
    log_decision din tradeall.py: un rand per TRANZITIE, sanitizat, try/except
    la scriere (un bug de jurnal nu are voie sa afecteze procesul gazda).

    Format: ts|symbol|signal|event|state|old_state|price|vel|vel_std
    Live: fisier rotit zilnic in logger/. Backtest: fisier FLAT (fixed_path)."""

    def __init__(self, out_dir: str = "logger", fixed_path: str | None = None):
        self.out_dir = out_dir
        self.fixed_path = fixed_path

    @staticmethod
    def _sanitize(value) -> str:
        return str(value).replace("|", "/").replace("\n", " ") if value is not None else ""

    def _path(self) -> str:
        if self.fixed_path:
            return self.fixed_path
        return os.path.join(self.out_dir, f"tradeall_shadow_{date.today().isoformat()}.log")

    def log_transition(self, ts: float, symbol: str, signal: str, state, old_state,
                       price, vel="", vel_std="") -> None:
        try:
            if not self.fixed_path:
                os.makedirs(self.out_dir, exist_ok=True)
            cols = [ts, symbol, signal, "trend_start", state, old_state, price, vel, vel_std]
            with open(self._path(), "a", encoding="utf-8") as f:
                f.write("|".join(self._sanitize(c) for c in cols) + "\n")
        except Exception as e:  # noqa: BLE001 — observational, nu oprim gazda
            print(f"[shadow_signals] eroare scriere jurnal shadow: {e}")


class ShadowSet:
    """Toate semnalele shadow pentru un set de simboluri + jurnalul lor.
    Un singur apel per evaluare: update(symbol, ts, price, epsilon,
    big_prices, big_sample_rate) -> dict de chei pt snapshot.

    state_path: fisier JSON propriu cu ultima stare per simbol. NECESAR live:
    cache_instant_trend.json e scris de PROCESUL cacheManager (writer), nu de
    tradeall — cheile adaugate de tradeall in snapshot raman doar in memoria
    lui. Monitorul citeste acest fisier si il combina cu snapshot-ul."""

    def __init__(self, journal: ShadowJournal | None = None,
                 state_path: str | None = None, state_min_interval: float = 1.0):
        self.journal = journal or ShadowJournal()
        self.state_path = state_path
        self.state_min_interval = state_min_interval
        self._state: dict = {}
        self._last_state_write = 0.0
        self._kalman: dict = {}

    def _write_state(self, now: float) -> None:
        if not self.state_path or (now - self._last_state_write) < self.state_min_interval:
            return
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                import json
                json.dump(self._state, f)
            os.replace(tmp, self.state_path)
            self._last_state_write = now
        except Exception as e:  # noqa: BLE001
            print(f"[shadow_signals] eroare scriere stare shadow: {e}")

    def update(self, symbol: str, ts: float, price: float, epsilon: float | None,
               big_prices, big_sample_rate: float) -> dict:
        kf = self._kalman.get(symbol)
        if kf is None:
            kf = self._kalman[symbol] = KalmanTrend()
        k = kf.update(ts, price, epsilon)

        if k["trend"] != k["old_trend"]:
            self.journal.log_transition(ts, symbol, "kalman", k["trend"], k["old_trend"],
                                         price, k["vel"], k["vel_std"])

        v1h = vol_1h_pct(big_prices, big_sample_rate)
        adapt_re, adapt_dca = adaptive_thresholds(v1h)
        fields = {
            "kalman_vel": k["vel"], "kalman_vel_std": k["vel_std"],
            "kalman_trend": k["trend"],
            "vol_1h_pct": v1h, "adapt_reentry_pct": adapt_re, "adapt_dca_pct": adapt_dca,
        }
        self._state[symbol] = {**fields, "ts": ts, "price": price}
        self._write_state(ts)
        return fields

"""replay_trend_source.py — a trend signal computed from the REPLAYED history (not from
cache_instant_trend.json, care e scris LIVE), pt backtest-uri monitortrades.

It uses EXACTLY the same PriceWindow class (pricewindow.py) and formula
(get_instant_trend: (slope_full + gradient_recent)/2 -> semn) ca live
(cacheManager.py, after the 29 Jul race fix — see below; identical also to
formula proprie a lui tradeall.handle_symbol), alimentata manual din tick-urile de
replay — acelasi tipar deja folosit de offline/backtests/tradeall.py pt ferestrele lui.

Why we do NOT model "tradeall absent": tradeall.py ALWAYS runs live (it is not an
optional process) — so this signal is always available, exactly as in live. Neutral
(gradient≈0) appears ONLY when the market itself has no clear direction in the
chosen window, never as a fallback for a process that might not be running.

Context (29 iul, investigatie cursa fast/slow in cacheManager.py): masurat empiric
pe istoric real ca cele 2 cai (rapida=get_recent_gradient simplu, lenta=
get_instant_trend bogat) difera in SEMN la 14.9% (BTC) / 21.0% (TAO) din tick-uri,
iar calea rapida singura isi schimba semnul la 32-35% din tick-uri (fereastra prea
mica/zgomotoasa pt un semnal de "trend" stabil). Fix aplicat live (cacheManager.py,
not yet committed at that date): the fast path no longer writes gradient_recent/final_trend,
only the slow path does. This module REPLICATES the slow-path formula (get_instant_trend),
the only one that reaches is_trend_up() in monitortrades.py today.

Parametrizabil pe durata ferestrei (window_seconds) — 3 orizonturi distincte, TOATE
valide (decizie user: nu se unifica intr-unul singur, fiecare model de trading
foloseste ce i se potriveste):
  - "instant"  ~3.7 min (implicit, identic cu WINDOW_SECONDS[0] din
                cacheManager.CachePriceShortTrendManager — ce citeste is_trend_up() azi)
  - "mediu"    ~1.5-6h  (orizontul "big" al lui tradeall, slope_big — azi nepublicat
                cross-process, dar calculabil identic aici pt teste A/B)
  - "lung"     nedefinit inca (fereastra exacta ramane de decis; suportat generic
                through the same window_seconds parameter, with no default)
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import pricewindow as pw

# Fereastra "instant" implicita — identica azi cu WINDOW_SECONDS[0] din
# cacheManager.CachePriceShortTrendManager (fereastra pe care se bazeaza is_trend_up()).
DEFAULT_WINDOW_SECONDS = 3.7 * 60

# Rangurile propuse (user, 29 iul) pt sweep-uri A/B de sensibilitate — NU valori
# implicite, doar puncte de plecare pt un backtest viitor pe orizont mediu/lung.
SMALL_WINDOW_RANGE_SEC = (60.0, 7 * 60.0)          # 1-7 minute
MEDIUM_WINDOW_RANGE_SEC = (1.5 * 3600.0, 6 * 3600.0)  # 1.5-6 ore
# Orizont lung: interval nedecis inca (user: "nu stiu") — de stabilit ulterior,
# pe baza unui backtest asupra intervalului, nu presupus a priori.


class ReplayTrendSource:
    """Un PriceWindow per simbol, alimentat manual din tick-urile de replay —
    izolat complet (nu atinge niciodata cache_instant_trend.json). Instantiaza-l
    o data per (simbol-set, window_seconds); pt teste A/B pe orizonturi diferite,
    use SEPARATE instances (do not reuse one across horizons)."""

    def __init__(self, symbols, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self.window_seconds = float(window_seconds)
        self._windows: Dict[str, pw.PriceWindow] = {
            s: pw.PriceWindow(s, window_size=200, window_seconds=self.window_seconds)
            for s in symbols
        }
        self._prev_ts: Dict[str, Optional[float]] = {s: None for s in symbols}

    def advance(self, symbol: str, ts: float, price: float) -> None:
        """Alimenteaza fereastra simbolului cu UN tick nou de replay. Trebuie
        apelat pt FIECARE tick, INAINTE de a citi is_trend_up() pt acelasi moment
        — otherwise the signal "sees" prices from the replay's future (look-ahead)."""
        win = self._windows.get(symbol)
        if win is None:
            return
        prev = self._prev_ts.get(symbol)
        if prev is not None and ts > prev:
            win.set_sample_rate(ts - prev)
        self._prev_ts[symbol] = ts
        win.process_price(price)

    def is_trend_up(self, symbol: str) -> bool:
        """Replicates EXACTLY the condition in monitortrades.is_trend_up() (slope>0 or
        slope==0 and gradient>0), reading from the REPLAY window, not the live file.
        Without enough data yet -> False (the same "neutral" fallback as today:
        monitortrades.is_trend_up() returns False when it has no snapshot yet)."""
        win = self._windows.get(symbol)
        if win is None or len(win.prices) < 2:
            return False
        final_trend, _growth_coefficient, _slope_full, gradient_recent = win.get_instant_trend()
        slope = gradient_recent
        gradient = final_trend
        return slope > 0 or (slope == 0 and gradient > 0)


# ──────────────────────────────────────────────────────────────────────────────
# Prototip (29 iul, idee user): fereastra mica CONFIRMABILA si DINAMICA — in loc
# de o singura fereastra FIXA (3.7 min, aleasa empiric de user din observatii),
# is_trend_up(symbol, timeout) accepta ORICE durata din rangul mic (14-30s pana
# at 9-11 min), computed ON DEMAND from a RAW buffer (ts, price) — not N
# ferestre PriceWindow precalculate pe bucket-uri fixe. "Confirmabil" = verifici
# agreement between several timeouts in the range before declaring a trend, instead
# sa te bazezi pe un singur esantion (acelasi principiu ca marja/confirmarea pe
# of the 2-window rule already used throughout the session's backtests).
#
# NOT DECIDED YET (to be validated empirically, not assumed): whether "confirmation
# timeout-uri" chiar bate o singura fereastra bine aleasa (3.7 min e deja
# empiric validat de user) — de comparat direct pe date reale inainte de orice
# concluzie.
# ──────────────────────────────────────────────────────────────────────────────

SMALL_TIMEOUT_RANGE_SEC = (14.0, 11 * 60.0)   # 14s - 11 min (user, 29 iul)


def _instant_trend_from_slice(prices: Sequence[float], sample_rate_sec: float
                               ) -> Tuple[int, float, float, float]:
    """Aceeasi formula ca PriceWindow.get_instant_trend(), aplicata direct pe o
    a list of prices (without rebuilding a PriceWindow) — for slices computed
    PE CERERE dintr-un buffer brut. Intoarce (final_trend, growth_coefficient,
    slope_full, gradient_recent), identic ca semnatura cu get_instant_trend().
    30 iul: subtire wrapper peste pw.instant_trend_from_prices() (sursa unica a
    of the formula, extracted so it is not duplicated — cacheManager.py now uses
    aceeasi functie pt fereastra dinamica live)."""
    return pw.instant_trend_from_prices(prices, sample_rate_sec)


class DynamicReplayTrendSource:
    """Buffer BRUT (ts, pret) per simbol, acoperind max(timeouts folosite) —
    in loc de o fereastra PriceWindow FIXA. is_trend_up_at(symbol, timeout_sec)
    it trims the buffer to the last timeout_sec and computes the formula ON DEMAND
    (not precomputed) — any timeout in the range, not just fixed buckets."""

    def __init__(self, symbols, max_timeout_sec: float = SMALL_TIMEOUT_RANGE_SEC[1]):
        self.max_timeout_sec = float(max_timeout_sec)
        self._buf: Dict[str, deque] = {s: deque() for s in symbols}   # [(ts, price), ...] crescator

    def advance(self, symbol: str, ts: float, price: float) -> None:
        buf = self._buf.get(symbol)
        if buf is None:
            return
        buf.append((ts, price))
        cutoff = ts - self.max_timeout_sec
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    def _slice(self, symbol: str, timeout_sec: float) -> List[Tuple[float, float]]:
        buf = self._buf.get(symbol)
        if not buf:
            return []
        cutoff = buf[-1][0] - timeout_sec
        return [(t, p) for t, p in buf if t >= cutoff]

    def _prices_and_rate(self, symbol: str, timeout_sec: float) -> Tuple[List[float], float]:
        pts = self._slice(symbol, timeout_sec)
        if len(pts) < 2:
            return [], pw.DEFAULT_SAMPLE_RATE_SEC
        timestamps = [t for t, _ in pts]
        prices = [p for _, p in pts]
        gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)
                if timestamps[i + 1] > timestamps[i]]
        sample_rate = float(np.median(gaps)) if gaps else pw.DEFAULT_SAMPLE_RATE_SEC
        return prices, sample_rate

    def is_trend_up_at(self, symbol: str, timeout_sec: float) -> bool:
        """is_trend_up(timeout) — cere idea user: fereastra mica DINAMICA, orice
        any duration in the range (14s-11min), not just a fixed 3.7min."""
        prices, sample_rate = self._prices_and_rate(symbol, timeout_sec)
        if len(prices) < 2:
            return False
        final_trend, _gc, _slope_full, gradient_recent = _instant_trend_from_slice(prices, sample_rate)
        return gradient_recent > 0 or (gradient_recent == 0 and final_trend > 0)

    def is_trend_up_confirmed(self, symbol: str, timeouts_sec: Sequence[float],
                               min_agree: Optional[int] = None) -> bool:
        """"Confirmable": True only if at least `min_agree` of the given timeouts
        agree that the trend is up (default min_agree=all — the most
        strict). Raspuns la ideea user: nu te baza pe UN singur esantion zgomotos."""
        votes = [self.is_trend_up_at(symbol, t) for t in timeouts_sec]
        threshold = min_agree if min_agree is not None else len(timeouts_sec)
        return sum(votes) >= threshold

    def should_wait(self, symbol: str, side: str, timeout_sec: float,
                    use_noise_gate: bool = False, epsilon_k: float = 1.0) -> bool:
        """True = ASTEAPTA (nu inca un moment bun sa executi o intentie de BUY/SELL
        in coada), False = OK, executa acum. BUY asteapta cat pretul SCADE, executa
        la PRIMUL semn de urcare (g>=0); SELL invers (asteapta cat urca, executa la
        primul semn de scadere, g<=0).

        Precizare user (29 iul, dupa masurare empirica): "prima tendinta USOARA de
        [inversare] -> intru" — orice semn, oricat de slab, e suficient. Implicit
        (use_noise_gate=False) no longer requires exceeding a noise threshold (eps) —
        varianta initiala (copiata fidel din cacheManager.is_favorable_to_wait) o
        cerea, si asta a fost EXACT problema gasita: la cele 2 evenimente reale
        testate, growth_coefficient era in interiorul lui eps (flat/zgomot la scara
        scurta) desi trendul mai larg (pe care se baza deja decizia lui tradeall de
        a firea) era deja confirmat — regula de zgomot tinea in asteptare MAI MULT
        exact cand semnalul era slab/marginal, opusul a ce se dorea. use_noise_gate=
        True pastreaza varianta veche (fidela cu is_favorable_to_wait) pt comparatie."""
        prices, _sample_rate = self._prices_and_rate(symbol, timeout_sec)
        if len(prices) < 3:
            return True   # date insuficiente -> comportament sigur: asteapta
        _final_trend, growth_coefficient, _slope_full, _gradient_recent = \
            _instant_trend_from_slice(prices, _sample_rate)
        g = growth_coefficient
        if use_noise_gate:
            arr = np.array(prices)
            eps = float(epsilon_k * np.std(np.gradient(arr)))
            if abs(g) <= eps:
                return True
        side = side.upper()
        if side == "BUY":
            return g < 0
        if side == "SELL":
            return g > 0
        return False

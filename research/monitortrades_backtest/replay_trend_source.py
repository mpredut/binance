"""replay_trend_source.py — semnal de trend calculat din istoricul REDAT (nu din
cache_instant_trend.json, care e scris LIVE), pt backtest-uri monitortrades.

Foloseste EXACT aceeasi clasa PriceWindow (pricewindow.py) si formula
(get_instant_trend: (slope_full + gradient_recent)/2 -> semn) ca live
(cacheManager.py, dupa fix-ul de cursa din 29 iul — vezi mai jos; identica si cu
formula proprie a lui tradeall.handle_symbol), alimentata manual din tick-urile de
replay — acelasi tipar deja folosit de tradeall_backtest.py pt ferestrele lui.

De ce NU modelam "tradeall absent": tradeall.py ruleaza MEREU pe live (nu e un
proces optional) — deci acest semnal e mereu disponibil, exact ca live. Neutru
(gradient≈0) apare DOAR cand piata insasi e fara directie clara in fereastra
aleasa, niciodata ca fallback pt un proces care ar putea sa nu ruleze.

Context (29 iul, investigatie cursa fast/slow in cacheManager.py): masurat empiric
pe istoric real ca cele 2 cai (rapida=get_recent_gradient simplu, lenta=
get_instant_trend bogat) difera in SEMN la 14.9% (BTC) / 21.0% (TAO) din tick-uri,
iar calea rapida singura isi schimba semnul la 32-35% din tick-uri (fereastra prea
mica/zgomotoasa pt un semnal de "trend" stabil). Fix aplicat live (cacheManager.py,
neconmis inca la data asta): calea rapida nu mai scrie gradient_recent/final_trend,
doar calea lenta le scrie. Acest modul REPLICA formula caii lente (get_instant_trend),
singura care ajunge azi la is_trend_up() din monitortrades.py.

Parametrizabil pe durata ferestrei (window_seconds) — 3 orizonturi distincte, TOATE
valide (decizie user: nu se unifica intr-unul singur, fiecare model de trading
foloseste ce i se potriveste):
  - "instant"  ~3.7 min (implicit, identic cu WINDOW_SECONDS[0] din
                cacheManager.CachePriceShortTrendManager — ce citeste is_trend_up() azi)
  - "mediu"    ~1.5-6h  (orizontul "big" al lui tradeall, slope_big — azi nepublicat
                cross-process, dar calculabil identic aici pt teste A/B)
  - "lung"     nedefinit inca (fereastra exacta ramane de decis; suportat generic
                prin acelasi parametru window_seconds, fara valoare implicita)
"""
from __future__ import annotations

from typing import Dict, Optional

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
    foloseste instante SEPARATE (nu reutiliza aceeasi intre orizonturi)."""

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
        — altfel semnalul "vede" preturi din viitorul replay-ului (look-ahead)."""
        win = self._windows.get(symbol)
        if win is None:
            return
        prev = self._prev_ts.get(symbol)
        if prev is not None and ts > prev:
            win.set_sample_rate(ts - prev)
        self._prev_ts[symbol] = ts
        win.process_price(price)

    def is_trend_up(self, symbol: str) -> bool:
        """Replica EXACT conditia din monitortrades.is_trend_up() (slope>0 sau
        slope==0 si gradient>0), citind din fereastra REPLAY, nu din fisierul live.
        Fara date suficiente inca -> False (acelasi fallback "neutru" ca azi:
        monitortrades.is_trend_up() intoarce False cand nu are inca snapshot)."""
        win = self._windows.get(symbol)
        if win is None or len(win.prices) < 2:
            return False
        final_trend, _growth_coefficient, _slope_full, gradient_recent = win.get_instant_trend()
        slope = gradient_recent
        gradient = final_trend
        return slope > 0 or (slope == 0 and gradient > 0)

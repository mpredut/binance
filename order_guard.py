# order_guard.py
"""Gard de profit AGNOSTIC de platforma.

Regula: nu CUMPARA peste ultimul SELL, nu VINDE sub ultimul BUY (cu o marja minima).
Decuplat de Binance: primeste un `provider` (orice obiect cu `last_opposite_fill(symbol,
order_type)`) si, optional, o referinta din fereastra (`window_ref`) calculata de apelant
(ex. min(sell)/max(buy) din Order cache-ul Binance). Asa ACEEASI logica de profit ruleaza
pe orice venue (Binance, Kraken HYPE, ...), nu doar inghesuita in bapi_placeorder.

Importa DOAR utils (fara provideri/cacheManager) -> zero risc de import circular.
RIDICA exceptie daca citirea referintei esueaza (provider.last_opposite_fill) -> apelantul
decide fail-closed. Returneaza True (poate plasa) / False (blocat).

Pragul de profit (%) e per-venue in `order_guard.conf` (text, gitignorat? NU — versionat ca
config nesensibil). Lipsa fisierului / valoare invalida -> default 1.15 (fail-safe)."""
import os
import utils as u

_MARGINS = None   # cache: {provider_lower: procent, "default": 1.15}


def _load_margins():
    """Citeste order_guard.conf (o data, cache-uit). Linii `provider = procent`, '#' comentariu.
    Fail-safe: lipsa fisier / parse invalid -> {'default': 1.15}."""
    global _MARGINS
    if _MARGINS is not None:
        return _MARGINS
    m = {"default": 1.15}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_guard.conf")
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                m[k.strip().lower()] = float(v.strip())
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[order_guard] conf invalid ({e}) — folosesc default 1.15")
    _MARGINS = m
    return m


def margin_for(provider_name):
    """Pragul minim de profit (%) pt un venue, din config (fallback 'default' = 1.15)."""
    m = _load_margins()
    return m.get((provider_name or "").lower(), m["default"])


def window_for(provider_name):
    """Fereastra (SECUNDE) pt referinta min/max time-windowed, per venue (cheia
    `<venue>_window_h` in conf). 0 = fara tier time-windowed (doar last_opposite_fill)."""
    m = _load_margins()
    key = (provider_name or "").lower() + "_window_h"
    hours = m.get(key, m.get("default_window_h", 0.0))
    return float(hours) * 3600.0


def window_reference(provider, symbol, order_type, window_s):
    """Referinta TIME-WINDOWED: min(sell) pt un BUY / max(buy) pt un SELL, din ordinele/fills
    OPUSE din ultimele window_s secunde (provider.get_orders). None daca fereastra e goala sau
    window_s<=0. RIDICA pe eroare de citire -> apelantul fail-closed (ca last_opposite_fill).
    Ignora preturile <=0 (defensiv). Acelasi tier 1 ca la Binance, dar agnostic."""
    if not window_s or window_s <= 0:
        return None
    opp = "SELL" if order_type.upper() == "BUY" else "BUY"
    recent = provider.get_orders(symbol, opp, window_s) or []
    prices = [float(o.get("price") or 0) for o in recent if float(o.get("price") or 0) > 0]
    if not prices:
        return None
    return min(prices) if order_type.upper() == "BUY" else max(prices)


def profit_guard(provider, symbol, order_type, price, profit_percentage, window_ref=None):
    """True = ordinul e profitabil fata de referinta (poate fi plasat); False = blocat.
    Referinta, in cascada:
      1) window_ref (daca apelantul o da — ex. min/max din fereastra time-windowed),
      2) altfel provider.last_opposite_fill(symbol, order_type) (la Binance: cache fills + API).
    Lipsa referintei (None / <=0) -> True (prima tranzactie, nimic de comparat)."""
    order_type = order_type.upper()
    ref = window_ref if window_ref is not None else provider.last_opposite_fill(symbol, order_type)
    if ref is None or ref <= 0:
        return True
    if order_type == "BUY":
        diff = u.value_diff_to_percent(ref, price)   # (ref_SELL - pret_BUY)/ref_SELL
    else:
        diff = u.value_diff_to_percent(price, ref)   # (pret_SELL - ref_BUY)/pret_SELL
    src = "fereastra" if window_ref is not None else "provider"
    print(f"[GARD] {order_type} {symbol}: ref {ref} ({src}), pret {price}, "
          f"diff {diff:.2f}%, prag {profit_percentage}%")
    if diff < profit_percentage:
        print(f"Diferenta procentuala ({diff:.2f}%) sub prag {profit_percentage}%. "
              f"Ordinul de {order_type} BLOCAT.")
        return False
    return True

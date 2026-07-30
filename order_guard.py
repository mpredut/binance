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
import time
import math
import utils as u

_MARGINS = None   # cache: {provider_lower: procent, "default": 1.15}


def _load_margins():
    """Citeste order_guard.conf (o data, cache-uit). Linii `cheie = valoare`, '#' comentariu.
    order_guard.conf e SURSA UNICA de adevar pt aceste valori. Dictionarul de mai jos e
    DOAR plasa de siguranta daca un rand lipseste din conf (ex. fisier trunchiat) — un
    SINGUR loc cu fallback-uri, ca sa NU existe valori 'default' imprastiate/duplicate prin
    functiile de mai jos (cerere user: sa nu fie confuzie intre valoarea reala din conf si
    un shadow hardcodat). Daca modifici o valoare, schimb-o in order_guard.conf, nu aici."""
    global _MARGINS
    if _MARGINS is not None:
        return _MARGINS
    m = {
        "default": 1.15,                       # prag profit (%) fallback
        "default_window_h": 0.0,               # fereastra gard profit (ore); 0 = doar last_opposite_fill
        "default_max_daily_trades": 25,        # plafon tranzactii/zi
        "default_safeback_sec": 48 * 3600 + 60,  # fereastra cautare tranzactii proprii (sec)
        "default_recent_transaction_sec": 180,   # anti-spam (sec)
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_guard.conf")
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().lower(); v = v.strip()
                try:                       # praguri/ore/weight = float; proxy = string (ex BTCUSDC)
                    m[k] = float(v)
                except ValueError:
                    m[k] = v
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
    hours = m.get(key, m["default_window_h"])
    return float(hours) * 3600.0


def weight_proxy_for(provider_name):
    """Symbol-ul al carui trend/gauss e PROXY cand symbol-ul curent n-are trend lung propriu
    (ex HYPE -> BTC pana are date). Cheia `<venue>_weight_proxy` sau `default_weight_proxy`.
    None = fara proxy (cade pe default 0.03)."""
    m = _load_margins()
    return m.get((provider_name or "").lower() + "_weight_proxy", m.get("default_weight_proxy"))


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


def weight_limit(provider, symbol, order_type, price, required_qty, base=None, quote=None):
    """Plafon de CANTITATE per ordin pe curba gauss (echivalentul agnostic al
    apply_weight_limit din bapi). Distribuie suma tranzactionabila proportional cu pozitia
    in trend -> nu vinzi/cumperi tot dintr-o data. AGNOSTIC: gauss-ul vine din priceAnalysis
    (symbol-ul are trend in _trend_syms, incl. HYPEUSD); 'traded 24h' din provider.get_orders
    (Kraken: cache-ul propriu); balanta din provider.free_balance. Returneaza qty plafonat
    (min(cerut, permis)). RIDICA pe eroare -> apelantul fail-closed (ca gardul)."""
    import math
    def _ok(w):
        return w is not None and not (isinstance(w, float) and math.isnan(w)) and w > 0

    def _gauss(sym):
        try:
            import priceAnalysis as pa
            return pa.get_weight_for_cash_permission_at_quant_time(sym, order_type)
        except Exception as e:
            print(f"[WEIGHT] {sym}: nu pot calcula gauss ({e})")
            return None

    weight = _gauss(symbol)                                 # gauss-ul propriu (daca symbol-ul are trend lung)
    if not _ok(weight):                                     # n-are trend propriu (ex HYPE) -> proxy (ex BTC)
        proxy = weight_proxy_for(getattr(provider, "name", ""))
        if proxy and proxy != symbol:
            pw = _gauss(proxy)
            if _ok(pw):
                weight = pw
                print(f"[WEIGHT] {symbol}: fara trend propriu -> proxy {proxy} (weight={weight})")
    if not _ok(weight):                                     # nici proxy -> default conservator
        weight = 0.03
    recent = provider.get_orders(symbol, order_type, 86400) or []      # acelasi side, ultimele 24h
    traded_value = sum(float(o.get("price", 0)) * float(o.get("qty", o.get("quantity", 0))) for o in recent)
    # available (in BASE), side-aware ca apply_weight_limit Binance (get_asset_info(order_type)):
    #   SELL -> balanta de BASE pe care o ai de vandut;
    #   BUY  -> cat BASE poti cumpara cu balanta de QUOTE (free_balance mapeaza USD->ZUSD pe Kraken).
    if order_type.upper() == "SELL":
        available = float(provider.free_balance(base or symbol) or 0.0)
    else:
        qbal = float(provider.free_balance(quote) or 0.0) if quote else 0.0
        available = (qbal / price) if price else 0.0
    total_ref = traded_value + available * price                       # tot ce-ai putea tranzactiona (quote)
    max_trade_value = total_ref * weight                               # plafon pe gauss
    remaining_value = max(0.0, max_trade_value - traded_value)         # cat mai poti azi
    remaining_qty = remaining_value / price if price else 0.0
    adjusted = min(required_qty, remaining_qty)
    print(f"[WEIGHT] {order_type} {symbol}: weight={weight} traded24h={traded_value:.2f} "
          f"avail={available:.6f} max={max_trade_value:.2f} remaining={remaining_value:.2f} "
          f"cerut={required_qty:.6f} -> {adjusted:.6f}")
    return adjusted


def max_daily_trades_for(provider_name):
    """Plafonul de tranzactii/zi pt un venue (din order_guard.conf; fallback in seed-ul
    din _load_margins). Valoarea o schimbi in order_guard.conf, nu aici."""
    m = _load_margins()
    key = (provider_name or "").lower() + "_max_daily_trades"
    return int(m.get(key, m["default_max_daily_trades"]))


def safeback_sec_for(provider_name):
    """Fereastra (SECUNDE) in care se cauta tranzactii pt plafonul zilnic, per venue
    (din order_guard.conf; fallback in seed-ul din _load_margins)."""
    m = _load_margins()
    key = (provider_name or "").lower() + "_safeback_sec"
    return float(m.get(key, m["default_safeback_sec"]))


def recent_transaction_sec_for(provider_name):
    """Fereastra anti-spam (SECUNDE): refuza un ordin nou pe acelasi symbol+side daca a
    mai fost unul recent, per venue (din order_guard.conf; fallback in seed-ul _load_margins)."""
    m = _load_margins()
    key = (provider_name or "").lower() + "_recent_transaction_sec"
    return float(m.get(key, m["default_recent_transaction_sec"]))


def daily_limit_guard(provider, symbol, order_type, max_daily_trades=None,
                      safeback_sec=None, recent_transaction_sec=None):
    """Plafon zilnic + anti-spam — SURSA UNICA (30 iul: Binance delega aici acum, din
    bapi_placeorder.if_place_safe_order(), in loc sa mentina o a doua implementare
    inline; vezi si Kraken/Hyperliquid, prin Instrument.place()). Foloseste
    provider.get_orders() (Kraken: cache-ul propriu; Binance: BinanceProvider, wrapper
    subtire peste bapi_allorders.get_trade_orders() — ACELASI apel ca inainte).
    Raporteaza ordinele PE ACELASI side (order_type) din ultimele `safeback_sec`;
    peste `max_daily_trades`/zi -> "daily_limit"; unul in ultimele
    `recent_transaction_sec` -> "recent_transaction".

    backdays = math.ceil(safeback_sec/86400) — ROTUNJIT IN SUS, identic cu formula
    istorica Binance (bapi_placeorder, din prima zi a mecanismului) — NU împărțire
    simpla, ca sa nu schimbe pragul efectiv fata de ce rula deja pe Binance de 18 luni.

    Returneaza (True, None) / (False, motiv). RIDICA pe eroare de citire (provider.get_orders)
    -> apelantul decide fail-closed (ca profit_guard/weight_limit)."""
    name = getattr(provider, "name", "")
    max_daily_trades = max_daily_trades if max_daily_trades is not None else max_daily_trades_for(name)
    safeback_sec = float(safeback_sec if safeback_sec is not None else safeback_sec_for(name))
    recent_transaction_sec = float(recent_transaction_sec if recent_transaction_sec is not None
                                   else recent_transaction_sec_for(name))
    order_type = order_type.upper()
    trades = provider.get_orders(symbol, order_type, safeback_sec) or []
    backdays = max(math.ceil(safeback_sec / 86400.0), 1)
    if len(trades) / backdays > max_daily_trades:
        print(f"[DAILY-LIMIT] {order_type} {symbol}: {len(trades)} tranzactii in "
              f"{safeback_sec/3600:.1f}h, plafon {max_daily_trades}/zi -> BLOCAT")
        return False, "daily_limit"
    cutoff_ms = time.time() * 1000 - recent_transaction_sec * 1000
    for t in trades:
        ts = t.get("timestamp")
        if ts is not None and float(ts) >= cutoff_ms:
            print(f"[DAILY-LIMIT] {order_type} {symbol}: tranzactie recenta "
                  f"(<{recent_transaction_sec:.0f}s) -> BLOCAT")
            return False, "recent_transaction"
    return True, None


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

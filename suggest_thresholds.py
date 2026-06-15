#!/usr/bin/env python3
"""
suggest_thresholds.py — FAZA 2: sugereaza praguri de alerta (UP/DOWN) pt monede,
din VOLATILITATEA lor reala (CoinGecko), nu dintr-un default orb.

Logica: la fiecare punct, calculeaza ca PriceChecker "cat a urcat fata de minimul pe
24h" si "cat a scazut fata de maximul pe 24h". Ia percentila (p85) a acestor miscari
=> pragul prinde sferturile de zile cu miscare MARE, nu zgomotul zilnic normal.

Output = linii gata de pus in market_alerts.conf. TU le revizuiesti (sunt provizorii;
moneda noua are istoric subtire -> rafineaza pe masura ce se aduna date).

  python3 suggest_thresholds.py SPCXX TAO BTC
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request

WINDOW = 24       # ~ore intr-o fereastra de 24h (granularitate orara CoinGecko)
PERCENTILE = 85   # pragul = miscarea de la care in sus consideram "notabil"
DAYS = 14
FLOOR = 3.0       # nu coborî pragul sub atat (altfel alerteaza pe zgomot)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def resolve_id(symbol):
    """Simbol -> (coingecko_id, simbol, nume). Prefera potrivirea exacta de simbol."""
    coins = _get(f"https://api.coingecko.com/api/v3/search?query={symbol}").get("coins", [])
    for c in coins:
        if c["symbol"].upper() == symbol.upper():
            return c["id"], c["symbol"].upper(), c.get("name", "")
    if coins:
        c = coins[0]
        return c["id"], c["symbol"].upper(), c.get("name", "")
    return None, None, None


def _perc(xs, pct):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * pct / 100))]


def thresholds_from_prices(prices, window=WINDOW, pct=PERCENTILE):
    """PUR (testabil): din seria de preturi -> (up, down, up_median, down_median)."""
    ups, downs = [], []
    for i in range(window, len(prices)):
        win = prices[i - window:i + 1]
        mn, mx, cur = min(win), max(win), prices[i]
        if mn > 0:
            ups.append((cur - mn) / mn * 100)
        if mx > 0:
            downs.append((mx - cur) / mx * 100)
    if not ups or not downs:
        return None
    return (
        max(round(_perc(ups, pct), 1), FLOOR),
        max(round(_perc(downs, pct), 1), FLOOR),
        round(statistics.median(ups), 1),
        round(statistics.median(downs), 1),
    )


def suggest(symbol, days=DAYS):
    cid, sym, name = resolve_id(symbol)
    if not cid:
        return None
    data = _get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart?vs_currency=usd&days={days}")
    prices = [p[1] for p in data.get("prices", []) if p and p[1]]
    if len(prices) < WINDOW + 5:
        return None
    res = thresholds_from_prices(prices)
    if not res:
        return None
    up, down, up_med, down_med = res
    return {"symbol": sym, "name": name, "up": up, "down": down,
            "up_median": up_med, "down_median": down_med, "points": len(prices)}


def main() -> int:
    syms = sys.argv[1:] or ["BTC", "TAO"]
    print(f"# praguri sugerate din volatilitate (p{PERCENTILE} pe {DAYS} zile) — REVIZUIESTE, apoi pune in market_alerts.conf")
    for s in syms:
        try:
            r = suggest(s)
        except Exception as e:  # noqa: BLE001
            print(f"# {s}: eroare ({e})"); continue
        if not r:
            print(f"# {s}: date insuficiente / negasit pe CoinGecko"); continue
        print(f"{r['symbol']:<8} = {r['up']} / {r['down']}    "
              f"# {r['name']} | tipic zilnic +{r['up_median']}/-{r['down_median']}% ({r['points']} puncte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

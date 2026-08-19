#!/usr/bin/env python3
"""
Backtest monitortrades.py (Faza 1, pas 2 din UNIFIED_BACKTEST_PLAN.md) pe
istoric REAL BTC/TAO, folosind ReplayMarketDataProvider — raspunde la
candidatii #4-5 din BACKTEST_CANDIDATES.md: sunt gain/lost/maxage per simbol
(instruments.conf) buni?

Metodologie (necesara, nu artefact): monitor_price_and_trade() GESTIONEAZA o
pozitie EXISTENTA — nu initiaza niciodata primul BUY fara un SELL anterior de
care sa reactioneze (verificat in cod: `if not (trade_orders_buy or
trade_orders_sell): return`). Un backtest "de la zero" ar sta degeaba tot
timpul. Simulam mentinerea CONTINUA a unei pozitii: de fiecare data cand NU
exista NICIO tranzactie (BUY sau SELL) in fereastra de varsta
(mt.maxage_days) — adica tocmai s-a inchis un ciclu (TP normal sau HARD-TP)
SAU pozitia anterioara a "expirat" fara sa se intample nimic — se re-seedeaza
un BUY nou la pretul curent, cu acelasi notional fix. Asta produce MULTE
cicluri independente peste 329 de zile (nu doar unul), comparabil ca metoda
cu sweep-urile Kraken/tradeall din aceeasi sesiune.

Foloseste valorile REALE din instruments.conf (nu constante arbitrare):
  BTCUSDC: gain=7.0% lost=3.3% maxage=7z hardtp=17%/0.5/6h
  TAOUSDC: gain=9.2% lost=4.9% maxage=17z hardtp=17%/0.5/6h

NU modifica monitortrades.py sau instruments.conf pe disc. Nu bate reteaua
niciodata (ReplayMarketDataProvider citeste doar din cachedb/cache_price_*.jsonl).

Rulare: python3 offline/research/monitortrades_backtest/run_replay_backtest.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.replay_provider import ReplayMarketDataProvider, load_price_series
from providers.market_api import MarketApi
from instrument import Instrument
import monitortrades as mt
from offline.research.monitortrades_backtest.replay_trend_source import (
    DEFAULT_WINDOW_SECONDS,
    ReplayTrendSource,
)

SEED_NOTIONAL_USD = 1000.0
SBS = 12 * 24 * 3600 + 60   # acelasi default ca live (MT_GUARD_WINDOW_DAYS=12)
FEE_PCT = 0.1


def _live_mt_params(section):
    """Citeste parametrii mt.* CURENTI dintr-o sectiune din instruments.conf —
    NU o copie hardcodata (gasit azi, 23 iul: SYMBOLS era o copie inghetata de
    dinainte sa adaugam mt.buy_budget/mt.max_budget — scheduled_pilot.py testa
    fara acea protectie, producand un rezultat catastrofal fals -$200k pe un
    "buy again" cu qty=1 BTC intreg, nemodelat corect din cauza asta)."""
    import configparser
    cp = configparser.ConfigParser()
    cp.read(os.path.join(ROOT, "instruments.conf"))
    if section not in cp:
        return {}
    return {k: v for k, v in cp[section].items() if k.startswith("mt.")}


class _LiveSymbols:
    """dict-like: SYMBOLS[symbol]["params"] citeste instruments.conf LA FIECARE
    ACCES (nu la import), ca sa nu poata deveni din nou o copie stale."""
    _SECTIONS = {"BTCUSDC": ("BINANCE_BTC", "BTC"), "TAOUSDC": ("BINANCE_TAO", "TAO")}

    def __getitem__(self, symbol):
        section, base = self._SECTIONS[symbol]
        return {"base": base, "params": _live_mt_params(section)}

    def items(self):
        return [(s, self[s]) for s in self._SECTIONS]


SYMBOLS = _LiveSymbols()


def _neutral_is_trend_up(symbol):
    """PASTRAT ca referinta/fallback manual (nu mai e default-ul lui run_symbol,
    vezi ReplayTrendSource mai jos) — inca folosit de scheduled_pilot.py::_run_one
    (loop separat, propriu, NEATINS in aceasta trecere — vezi 29 iul). Determinist,
    DOAR pt backtest: fara semnal de trend (neutru). GASIT 23 iul: is_trend_up()
    reala citeste cacheManager.get_short_trend_manager() — pt simboluri REALE
    (BTCUSDC/TAOUSDC), asta e cache-ul LIVE, actualizat CHIAR ACUM de
    tradeall.py/cacheManager.py, care ruleaza pe aceeasi masina. Rulare acelasi
    backtest de 2 ori a dat rezultate DIFERITE, pt ca trendul "live" citit se
    schimba intre rulari, contaminand un replay istoric cu starea REALA, curenta
    a pietei. False = exact ce ar intoarce is_trend_up() oricum pt un symbol FARA
    snapshot in cache (deja defaultul "sigur" din cod: "fara snapshot in cache ->
    neutru, nu blocheaza vanzarea pe castig")."""
    return False


def run_symbol(symbol, params, base, quiet=True, trend_window_seconds=DEFAULT_WINDOW_SECONDS):
    path = os.path.join(ROOT, "cachedb", f"cache_price_{symbol}.jsonl")
    series = load_price_series(path, symbol)
    if not series:
        sys.stderr.write(f"[{symbol}] fara istoric la {path}\n")
        return None

    if quiet:
        mt.log.disable_print() if hasattr(mt, "log") and hasattr(mt.log, "disable_print") else None

    provider = ReplayMarketDataProvider({symbol: series}, fee_pct=FEE_PCT)
    api = MarketApi([provider])
    inst = Instrument(name=symbol, symbol=symbol, provider="replay",
                      base=base, quote="USDC", params=dict(params), api=api)

    maxage_s = int(float(params["mt.maxage_days"]) * 24 * 3600)

    # 29 iul: is_trend_up() REPLICAT din istoricul redat (ReplayTrendSource), nu mai
    # neutralizat — vezi replay_trend_source.py pt de ce (cursa fast/slow investigata
    # si reparata live in cacheManager.py; tradeall.py ruleaza MEREU pe live, deci
    # semnalul e mereu disponibil, la fel ca aici). trend_window_seconds parametrizabil
    # pt teste A/B pe alt orizont (instant/mediu/lung), fara sa schimbe default-ul.
    trend_source = ReplayTrendSource([symbol], window_seconds=trend_window_seconds)

    orig_is_trend_up = mt.is_trend_up
    mt.is_trend_up = trend_source.is_trend_up
    try:
        first_price = provider.advance(symbol)
        if first_price is None:
            return None
        last_price = first_price
        trend_source.advance(symbol, provider.now(symbol), first_price)
        provider.place_order(symbol, "BUY", first_price, SEED_NOTIONAL_USD / first_price)
        n_seeds = 1
        n_ticks = 0

        while True:
            price = provider.advance(symbol)
            if price is None:
                break
            last_price = price
            n_ticks += 1
            # Alimenteaza fereastra de trend INAINTE de decizie — asa semnalul
            # reflecta doar ce s-ar fi vazut pana la acest tick (fara look-ahead).
            trend_source.advance(symbol, provider.now(symbol), price)

            buys = provider.get_orders(symbol, "BUY", since_s=maxage_s)
            sells = provider.get_orders(symbol, "SELL", since_s=maxage_s)
            if not buys and not sells:
                provider.place_order(symbol, "BUY", price, SEED_NOTIONAL_USD / price)
                n_seeds += 1
                continue

            try:
                mt.monitor_price_and_trade(inst, sbs=SBS, now_fn=lambda: provider.now(symbol))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[{symbol}] eroare in monitor_price_and_trade: {e}\n")

            if n_ticks % 20000 == 0:
                sys.stderr.write(f"[{symbol}] {n_ticks} tick-uri, seed-uri={n_seeds}\n")
    finally:
        mt.is_trend_up = orig_is_trend_up

    all_buys = provider.get_orders(symbol, "BUY", since_s=1e12)
    all_sells = provider.get_orders(symbol, "SELL", since_s=1e12)
    total_bought = sum(o["qty"] * o["price"] for o in all_buys)
    total_sold = sum(o["qty"] * o["price"] for o in all_sells)
    open_qty, _open_cost = provider.position(symbol)
    open_value = open_qty * last_price
    fees = sum(o["qty"] * o["price"] * FEE_PCT / 100 for o in all_buys + all_sells)
    net = total_sold - total_bought + open_value - fees

    bh_qty = SEED_NOTIONAL_USD / first_price
    buy_hold_net = (last_price - first_price) * bh_qty - 2 * bh_qty * first_price * FEE_PCT / 100

    result = dict(symbol=symbol, ticks=n_ticks, seeds=n_seeds, buys=len(all_buys), sells=len(all_sells),
                  net=round(net, 2), buy_hold=round(buy_hold_net, 2),
                  first_price=first_price, last_price=last_price,
                  trend_window_sec=trend_window_seconds)
    sys.stderr.write(f"[{symbol}] REZULTAT: {result}\n")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", choices=list(SYMBOLS._SECTIONS), default=None,
                     help="ruleaza DOAR acest simbol (implicit: BTCUSDC + TAOUSDC)")
    ap.add_argument("--trend-window-sec", type=float, default=DEFAULT_WINDOW_SECONDS,
                     help=f"orizontul ferestrei pt ReplayTrendSource (secunde), implicit "
                          f"{DEFAULT_WINDOW_SECONDS:.0f}s (~instant, 3.7min, ce citeste "
                          f"is_trend_up() azi pe live). Pt teste A/B: mic 60-420 (1-7 min), "
                          f"mediu 5400-21600 (1.5-6h, ca slope_big al lui tradeall).")
    args = ap.parse_args()

    targets = [args.symbol] if args.symbol else list(SYMBOLS._SECTIONS)
    for symbol in targets:
        cfg = SYMBOLS[symbol]
        run_symbol(symbol, cfg["params"], cfg["base"], trend_window_seconds=args.trend_window_sec)

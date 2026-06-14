#!/usr/bin/env python3
"""
market_data.py — date de piata via Yahoo Finance (pret, curs FX, detectie tranzactionare reala).
"""

from __future__ import annotations

import json
import time

from ipo_common import http_get, log

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_UA = {"User-Agent": "Mozilla/5.0 (ipo-watch)"}


def t212_to_yahoo(t212_ticker: str) -> str:
    """NVDA_US_EQ -> NVDA  (pentru cautare pret pe Yahoo)."""
    return t212_ticker.split("_")[0]


def _chart(sym: str, rng: str = "1d", interval: str = "5m"):
    """Returneaza (meta, bars) de pe Yahoo chart. bars = [(ts, close, volume), ...]
    cu close ne-null. Seria intraday e mai PROASPATA decat meta — la o listare noua
    meta poate ramane statuta (ex SPCX: vol=0/pret vechi desi se tranzactioneaza)."""
    status, body = http_get(YAHOO_CHART.format(sym=sym) + f"?range={rng}&interval={interval}",
                            headers=_UA)
    if status != 200 or not body:
        return None, []
    try:
        data = json.loads(body)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None, []
        meta = result.get("meta", {})
        ts = result.get("timestamp") or []
        q = ((result.get("indicators", {}).get("quote") or [{}])[0])
        closes = q.get("close") or []
        vols = q.get("volume") or []
        bars = [(t, c, (vols[i] if i < len(vols) else None))
                for i, (t, c) in enumerate(zip(ts, closes)) if c is not None]
        return meta, bars
    except (ValueError, KeyError, TypeError):
        return None, []


def get_price_usd(sym: str) -> float | None:
    """Pret curent (NVDA, SPCX, USDRON=X...). Prefera ultima bara din serie
    (mai proaspata) si cade pe meta.regularMarketPrice doar daca seria lipseste."""
    meta, bars = _chart(sym)
    if bars:
        return bars[-1][1]
    if meta:
        return meta.get("regularMarketPrice") or None
    return None


def get_usd_ron() -> float:
    """Curs USD/RON curent. Fallback 4.65 daca feed-ul nu raspunde."""
    rate = get_price_usd("USDRON=X")
    if rate and rate > 1:
        return rate
    log("  ! curs USD/RON indisponibil, folosesc fallback 4.65")
    return 4.65


def get_eur_usd() -> float:
    """Curs EUR->USD curent (cati USD intr-un EUR). Fallback 1.08."""
    rate = get_price_usd("EURUSD=X")
    if rate and rate > 0.5:
        return rate
    log("  ! curs EUR/USD indisponibil, folosesc fallback 1.08")
    return 1.08


def check_market(sym: str) -> dict | None:
    """Returneaza dict cu 'trading'=True DOAR daca simbolul se tranzactioneaza cu adevarat.

    Evita falsul pozitiv cu placeholder-ul de IPO (pret fix, volum 0):
    cere volum > 0, ultima tranzactie recenta (<15 min) si o stare de piata activa.
    """
    meta, bars = _chart(sym)
    if meta is None and not bars:
        return None
    meta = meta or {}

    price    = meta.get("regularMarketPrice")
    meta_vol = meta.get("regularMarketVolume") or 0
    state    = (meta.get("marketState") or "").upper()
    last_ts  = meta.get("regularMarketTime")

    age_sec = None
    if last_ts:
        try:
            age_sec = time.time() - float(last_ts)
        except (TypeError, ValueError):
            pass

    # --- semnale din SERIA intraday (robuste la meta statuta) ---
    series_age = series_vol = None
    series_price = None
    series_moved = False
    if bars:
        recent = bars[-6:]                       # ~ultimele 30 min
        series_age = time.time() - float(bars[-1][0])
        series_price = bars[-1][1]
        series_vol = sum(v for _, _, v in recent if v) or 0
        cs = [c for _, c, _ in recent]
        series_moved = len(cs) >= 2 and (max(cs) - min(cs)) > 0

    fresh_meta = age_sec is not None and age_sec < 15 * 60
    fresh_series = series_age is not None and series_age < 20 * 60
    live_state = state in ("REGULAR", "PRE", "PREPRE", "POST", "POSTPOST")

    # 'launched' = a tranzactionat cu adevarat. Acum: volum pe meta SAU serie
    # intraday proaspata cu tranzactionare reala (volum recent sau pret in miscare).
    # Asa prinde SPCX-ul (meta statuta vol=0, dar seria avea bare live la 164) si
    # NU se pacaleste de placeholder-ul pre-IPO (fara serie / serie plata).
    launched = (bool(price) and meta_vol > 0) or \
               (fresh_series and ((series_vol or 0) > 0 or series_moved))
    really_trading = launched and (fresh_meta or fresh_series) and (live_state or series_moved)

    age_min = None
    eff_age = series_age if series_age is not None else age_sec
    if eff_age is not None:
        age_min = round(eff_age / 60, 1)

    return {
        "price":    series_price or price,   # prefera pretul din serie (mai proaspat)
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "volume":   meta_vol or (series_vol or 0),
        "state":    state or "?",
        "age_min":  age_min,
        "name":     meta.get("longName") or meta.get("shortName") or "",
        "trading":  really_trading,   # se tranzactioneaza ACUM
        "launched": launched,         # a inceput sa se tranzactioneze
    }

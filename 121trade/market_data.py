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


def get_price_usd(sym: str) -> float | None:
    """Pret curent pentru orice simbol Yahoo (NVDA, SPCX, USDRON=X...)."""
    status, body = http_get(YAHOO_CHART.format(sym=sym), headers=_UA)
    if status != 200 or not body:
        return None
    try:
        data = json.loads(body)
        meta = ((data.get("chart", {}).get("result") or [{}])[0]).get("meta", {})
        return meta.get("regularMarketPrice") or None
    except (ValueError, KeyError, TypeError):
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
    status, body = http_get(YAHOO_CHART.format(sym=sym), headers=_UA)
    if status != 200 or not body:
        return None
    try:
        data = json.loads(body)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
    except (ValueError, KeyError, TypeError):
        return None

    price   = meta.get("regularMarketPrice")
    volume  = meta.get("regularMarketVolume") or 0
    state   = (meta.get("marketState") or "").upper()
    last_ts = meta.get("regularMarketTime")

    age_sec = None
    if last_ts:
        try:
            age_sec = time.time() - float(last_ts)
        except (TypeError, ValueError):
            pass

    fresh = age_sec is not None and age_sec < 15 * 60
    live_state = state in ("REGULAR", "PRE", "PREPRE", "POST", "POSTPOST")
    really_trading = bool(price) and volume > 0 and fresh and live_state

    return {
        "price":    price,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "volume":   volume,
        "state":    state or "?",
        "age_min":  round(age_sec / 60, 1) if age_sec is not None else None,
        "name":     meta.get("longName") or meta.get("shortName") or "",
        "trading":  really_trading,
    }

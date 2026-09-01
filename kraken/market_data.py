#!/usr/bin/env python3
"""
market_data.py — Kraken pair-price and availability tracking.

Kraken prices come directly from the public Ticker endpoint rather than Yahoo.
Available, analogous to SPCX launched, means the pair exists in AssetPairs and has
a valid price. HYPE qualifies; an unlisted future pair does not.
"""

from __future__ import annotations

from kraken_common import log
from kraken_client import KrakenClient, KrakenError


def get_price(client: KrakenClient, pair: str) -> float | None:
    """Return the latest pair price, e.g. HYPEUSD, or None when unavailable."""
    try:
        return client.last_price(pair)
    except KrakenError as e:
        log(f"  ! pret {pair} indisponibil: {e}")
        return None


def pair_available(client: KrakenClient, pair: str) -> dict | None:
    """Return pair metadata when LISTED and tradable on Kraken, otherwise None.

    Used as a launch detector; the bot waits while the pair is absent.
    """
    try:
        info = client.pair_info(pair)
    except KrakenError:
        return None
    if not info:
        return None
    # Status 'online' means tradable; Kraken also reports cancel/post/limit/reduce-only.
    status = info.get("status", "online")
    if status not in ("online", "limit_only", "post_only"):
        log(f"  [market] {pair} listed but status={status} (still not tradable)")
        return None
    return info


def pair_precision(info: dict) -> tuple[int, int, float]:
    """Return (price decimals, volume decimals, minimum order) from pair metadata."""
    price_dec = int(info.get("pair_decimals", 2))
    vol_dec = int(info.get("lot_decimals", 8))
    try:
        ordermin = float(info.get("ordermin", 0) or 0)
    except (TypeError, ValueError):
        ordermin = 0.0
    return price_dec, vol_dec, ordermin

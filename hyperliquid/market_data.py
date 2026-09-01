#!/usr/bin/env python3
"""
market_data.py — Hyperliquid coin price and availability.
Price comes from public all_mids; available means the coin is in the perpetual universe.
"""

from __future__ import annotations

from common import log
from hl_client import HLClient


def get_price(client: HLClient, coin: str) -> float | None:
    return client.mid(coin)


def coin_available(client: HLClient, coin: str) -> bool:
    try:
        return client.coin_listed(coin) and client.mid(coin) is not None
    except Exception as e:  # noqa: BLE001
        log(f"  ! coin_available({coin}) failed: {e}")
        return False

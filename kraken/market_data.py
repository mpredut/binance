#!/usr/bin/env python3
"""
market_data.py — urmarirea pretului si disponibilitatii unei perechi pe Kraken.

Pe Kraken, pretul vine direct din endpoint-ul public Ticker (nu Yahoo).
"Disponibil" (analog cu 'launched' la SPCX) = perechea exista in AssetPairs
si are un pret valid. Pentru HYPE -> da; pentru un SPCX inca nelistat -> nu.
"""

from __future__ import annotations

from common import log
from kraken_client import KrakenClient, KrakenError


def get_price(client: KrakenClient, pair: str) -> float | None:
    """Ultimul pret pentru pereche (ex HYPEUSD). None daca indisponibil."""
    try:
        return client.last_price(pair)
    except KrakenError as e:
        log(f"  ! pret {pair} indisponibil: {e}")
        return None


def pair_available(client: KrakenClient, pair: str) -> dict | None:
    """Returneaza info-ul perechii daca e LISTATA si tranzactionabila pe Kraken, altfel None.

    Folosit ca detector de 'lansare': cat timp perechea nu apare, botul asteapta.
    """
    try:
        info = client.pair_info(pair)
    except KrakenError:
        return None
    if not info:
        return None
    # status 'online' = tranzactionabil. (Kraken: online/cancel_only/post_only/limit_only/reduce_only)
    status = info.get("status", "online")
    if status not in ("online", "limit_only", "post_only"):
        log(f"  [market] {pair} listat dar status={status} (inca netranzactionabil)")
        return None
    return info


def pair_precision(info: dict) -> tuple[int, int, float]:
    """Din info-ul perechii: (zecimale_pret, zecimale_volum, ordin_minim)."""
    price_dec = int(info.get("pair_decimals", 2))
    vol_dec = int(info.get("lot_decimals", 8))
    try:
        ordermin = float(info.get("ordermin", 0) or 0)
    except (TypeError, ValueError):
        ordermin = 0.0
    return price_dec, vol_dec, ordermin

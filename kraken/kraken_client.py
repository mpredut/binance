#!/usr/bin/env python3
"""
kraken_client.py — client REST minimalist pentru Kraken (Spot).

Public  (fara chei):  ticker, asset_pairs, pair_info
Private (cu chei):    balance, add_order, cancel_order, query_orders, open_orders

Autentificare Kraken (diferita de T212):
    API-Key  : cheia publica (header)
    API-Sign : HMAC-SHA512 peste  urlpath + SHA256(nonce + postdata),
               cu secretul (base64) drept cheie, rezultat base64.
Vezi self-test-ul de la finalul fisierului (validat pe vectorul din docs Kraken).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse

from common import http_get, http_post_form, log

API_URL = "https://api.kraken.com"

# ─── Cache TTL partajat (per-proces) pt call-urile de CITIRE ──────────────────
# Kraken numara apelurile API pe cheie (rate-limit). Mai multi consumatori (monitortrades
# via provider, kraken_bot, trailing, xstock_watch) + gardul de profit (TradesHistory la
# fiecare plasare, de 2x: window + last_opposite_fill) lovesc des aceleasi endpointuri.
# Cache-uim citirile cu TTL scurt, PARTAJAT intre TOATE instantele KrakenClient din proces.
# Metodele de SCRIERE (AddOrder/CancelOrder) INVALIDEAZA starea de cont -> gardul/boturile
# vad IMEDIAT propria tranzactie (zero fereastra de staleness pe actiunile proprii).
# Nu acopera cross-PROCES (fiecare proces are cache propriu); TTL-ul margineste decalajul.
_CACHE = {}                       # (method, params_key) -> (expiry_ts, result)
_CACHE_LOCK = threading.Lock()
_READ_TTL = {                     # secunde; metodele NElistate NU se cacheaza (ex. QueryOrders)
    "Ticker": 3.0, "AssetPairs": 3600.0,
    "Balance": 15.0, "TradesHistory": 20.0, "ClosedOrders": 20.0, "OpenOrders": 5.0,
}
_WRITE_METHODS = ("AddOrder", "CancelOrder", "CancelAll")
_INVALIDATE_ON_WRITE = ("Balance", "TradesHistory", "ClosedOrders", "OpenOrders")


def _params_key(params: dict) -> tuple:
    return tuple(sorted((str(k), str(v)) for k, v in params.items() if k != "nonce"))


def _cache_get(method: str, params: dict):
    with _CACHE_LOCK:
        hit = _CACHE.get((method, _params_key(params)))
    if hit and hit[0] > time.time():
        return True, hit[1]
    return False, None


def _cache_put(method: str, params: dict, ttl: float, result) -> None:
    with _CACHE_LOCK:
        _CACHE[(method, _params_key(params))] = (time.time() + ttl, result)


def _cache_invalidate(methods) -> None:
    with _CACHE_LOCK:
        for k in [k for k in _CACHE if k[0] in methods]:
            _CACHE.pop(k, None)


class KrakenError(Exception):
    pass


class KrakenClient:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""

    # ----- semnatura -----------------------------------------------------------
    @staticmethod
    def _signature(urlpath: str, data: dict, secret: str) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _private(self, method: str, data: dict | None = None, fresh: bool = False) -> dict:
        if not self.api_key or not self.api_secret:
            raise KrakenError("Lipsesc cheile Kraken (verifica KRAKEN_API_KEY_BOT/_TRAIL/_CACHE in kraken/.env)")
        data = dict(data or {})
        ttl = _READ_TTL.get(method)
        if ttl and not fresh:                       # citire cache-uibila -> serveste din cache daca e proaspat
            ok, val = _cache_get(method, data)
            if ok:
                return val
        urlpath = f"/0/private/{method}"
        # nonce in nanosecunde: maxim monoton, depaseste orice nonce (ms/us) folosit anterior pe cheie
        data["nonce"] = str(time.time_ns())
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._signature(urlpath, data, self.api_secret),
        }
        status, body = http_post_form(API_URL + urlpath, data, headers=headers)
        result = self._parse(status, body)
        if ttl:
            _cache_put(method, data, ttl, result)
        if method in _WRITE_METHODS:                # AddOrder/CancelOrder -> starea de cont s-a schimbat
            _cache_invalidate(_INVALIDATE_ON_WRITE)
        return result

    def _public(self, method: str, params: dict | None = None, fresh: bool = False) -> dict:
        params = dict(params or {})
        ttl = _READ_TTL.get(method)
        if ttl and not fresh:
            ok, val = _cache_get(method, params)
            if ok:
                return val
        url = f"{API_URL}/0/public/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        status, body = http_get(url)
        result = self._parse(status, body)
        if ttl:
            _cache_put(method, params, ttl, result)
        return result

    @staticmethod
    def _parse(status: int, body: bytes) -> dict:
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise KrakenError(f"raspuns invalid (HTTP {status})")
        if payload.get("error"):
            raise KrakenError(", ".join(payload["error"]))
        return payload.get("result", {})

    # ----- PUBLIC --------------------------------------------------------------
    def asset_pairs(self) -> dict:
        return self._public("AssetPairs")

    def pair_info(self, pair: str) -> dict | None:
        """Info pereche: precizie pret/volum, ordin minim. None daca nu exista."""
        res = self._public("AssetPairs", {"pair": pair})
        if not res:
            return None
        return next(iter(res.values()))

    def ticker(self, pair: str) -> dict | None:
        res = self._public("Ticker", {"pair": pair})
        return next(iter(res.values())) if res else None

    def last_price(self, pair: str) -> float | None:
        t = self.ticker(pair)
        try:
            return float(t["c"][0]) if t else None      # 'c' = ultima tranzactie [pret, vol]
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    # ----- PRIVATE -------------------------------------------------------------
    def balance(self) -> dict:
        """Solduri pe active. {asset: cantitate}."""
        return self._private("Balance")

    def add_order(self, pair: str, side: str, volume: float, price: float | None = None,
                  ordertype: str = "limit", validate: bool = False) -> dict:
        """Plaseaza ordin. side='buy'|'sell'. validate=True -> doar valideaza (nu plaseaza)."""
        data = {
            "pair": pair,
            "type": side,
            "ordertype": ordertype,
            "volume": f"{volume}",
        }
        if ordertype == "limit" and price is not None:
            data["price"] = f"{price}"
        if validate:
            data["validate"] = "true"
        return self._private("AddOrder", data)

    def cancel_order(self, txid: str) -> dict:
        return self._private("CancelOrder", {"txid": txid})

    def query_orders(self, txids: str) -> dict:
        """Status ordine dupa txid (merge si pt cele inchise — fara 404 ca la T212)."""
        return self._private("QueryOrders", {"txid": txids})

    def open_orders(self) -> dict:
        return self._private("OpenOrders").get("open", {})


# ---------------------------------------------------------------------------
# Self-test semnatura (vectorul din documentatia Kraken) — ruleaza:
#   python3 kraken_client.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    secret = ("kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRz"
              "BHCd3pd5nE9qa99HAZtuZuj6F1huXg==")
    data = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSD",
            "price": 37500, "type": "buy", "volume": 1.25}
    expected = ("4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS"
                "8MPtnRfp32bAb0nmbRn6H8ndwLUQ==")
    got = KrakenClient._signature("/0/private/AddOrder", data, secret)
    ok = got == expected
    log(f"semnatura self-test: {'OK ✅' if ok else 'ESUAT ❌'}")
    if not ok:
        log(f"  asteptat: {expected}")
        log(f"  obtinut : {got}")

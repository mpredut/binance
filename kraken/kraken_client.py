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
import time
import urllib.parse

from common import http_get, http_post_form, log

API_URL = "https://api.kraken.com"


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

    def _private(self, method: str, data: dict | None = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise KrakenError("Lipsesc cheile Kraken (KRAKEN_API_KEY / KRAKEN_API_SECRET)")
        urlpath = f"/0/private/{method}"
        data = dict(data or {})
        # nonce in nanosecunde: maxim monoton, depaseste orice nonce (ms/us) folosit anterior pe cheie
        data["nonce"] = str(time.time_ns())
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._signature(urlpath, data, self.api_secret),
        }
        status, body = http_post_form(API_URL + urlpath, data, headers=headers)
        return self._parse(status, body)

    def _public(self, method: str, params: dict | None = None) -> dict:
        url = f"{API_URL}/0/public/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        status, body = http_get(url)
        return self._parse(status, body)

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

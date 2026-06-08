#!/usr/bin/env python3
"""
t212_client.py — client minimalist pentru API-ul Trading 212.

Acopera doar ce ne trebuie: listare instrumente, plasare ordin LIMIT, status ordin.
Schema ordinului LIMIT (confirmata de docs):
    POST /equity/orders/limit
    {"ticker": "...", "quantity": <+BUY/-SELL>, "limitPrice": ..., "timeValidity": "DAY"|"GOOD_TILL_CANCEL"}
NU exista camp "side"; NU e "instrumentTicker".
"""

from __future__ import annotations

import base64
import json

from ipo_common import http_get, http_post_json, log

LIVE_BASE = "https://live.trading212.com/api/v0"
DEMO_BASE = "https://demo.trading212.com/api/v0"

_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"


class T212Client:
    def __init__(self, api_key: str, api_secret: str | None = None, env: str = "live"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.env = (env or "live").lower()
        self.base = DEMO_BASE if self.env == "demo" else LIVE_BASE

    # -- auth / headers --------------------------------------------------------
    def _auth(self) -> str:
        if self.api_secret:
            token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
            return f"Basic {token}"
        return self.api_key

    def _headers(self) -> dict:
        return {
            "Authorization": self._auth(),
            "User-Agent": _BROWSER_UA,  # evita 403 Cloudflare
            "Accept": "application/json",
        }

    # -- instrumente -----------------------------------------------------------
    def list_instruments(self) -> list[dict] | None:
        status, body = http_get(f"{self.base}/equity/metadata/instruments", headers=self._headers())
        if status == 429:
            log("  ! T212 rate limit (429)")
            return None
        if status in (401, 403):
            log(f"  ! T212 auth esuat ({status}) - verifica cheia")
            return None
        if status != 200 or not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def search_instruments(self, ticker_substr: str, name_patterns: tuple[str, ...]) -> list[dict] | None:
        """Cauta instrumente dupa substring in ticker SAU pattern in nume/shortName."""
        instruments = self.list_instruments()
        if instruments is None:
            return None
        hits = []
        for ins in instruments:
            ticker = str(ins.get("ticker", ""))
            name   = str(ins.get("name", "")).lower()
            short  = str(ins.get("shortName", "")).lower()
            if (
                ticker_substr.upper() in ticker.upper()
                or any(p in name  for p in name_patterns)
                or any(p in short for p in name_patterns)
            ):
                hits.append(ins)
        return hits or None

    # -- ordine ----------------------------------------------------------------
    def place_limit_order(
        self,
        ticker: str,
        quantity: float,
        limit_price: float,
        validity: str = "DAY",
    ) -> tuple[int, dict]:
        """Plaseaza ordin LIMIT. Returneaza (http_status, payload_raspuns)."""
        payload = {
            "ticker":       ticker,
            "quantity":     round(quantity, 2),   # pozitiv = BUY
            "limitPrice":   round(limit_price, 2),
            "timeValidity": validity,
        }
        log(f"  [ORDER] payload: {json.dumps(payload)}")
        status, body = http_post_json(
            f"{self.base}/equity/orders/limit",
            payload=payload,
            headers=self._headers(),
        )
        try:
            data = json.loads(body) if body else {}
        except ValueError:
            data = {"raw": body.decode(errors="replace")[:500]}
        return status, data

    def get_order_status(self, order_id) -> dict | None:
        status, body = http_get(f"{self.base}/equity/orders/{order_id}", headers=self._headers())
        if status != 200:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def cancel_order(self, order_id) -> bool:
        """Anuleaza un ordin dupa id. Returneaza True daca a fost acceptat."""
        from ipo_common import http_request
        status, _ = http_request("DELETE", f"{self.base}/equity/orders/{order_id}",
                                 headers=self._headers())
        ok = status in (200, 201, 204)
        if not ok:
            log(f"  ! [T212] cancel ordin {order_id} -> HTTP {status}")
        return ok

    def get_portfolio(self) -> list[dict] | None:
        """Pozitiile deschise din cont (pentru reconciliere)."""
        status, body = http_get(f"{self.base}/equity/portfolio", headers=self._headers())
        if status != 200 or not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

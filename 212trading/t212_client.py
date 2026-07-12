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
import os
import threading
import time

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
        # Throttle COMUN: un singur client poate fi partajat de mai multe threaduri
        # (un thread per activ). Serializam + spatiem apelurile ca sa nu lovim
        # rate-limit-ul T212 (429). T212_MIN_GAP_SEC = pauza minima intre apeluri.
        self._lock = threading.Lock()
        self._last = 0.0
        self._min_gap = float(os.environ.get("T212_MIN_GAP_SEC", "0.3"))
        # Cache scurt PARTAJAT pt portofoliu: e la nivel de CONT (un apel = toate pozitiile),
        # deci cele N threaduri (un activ fiecare) nu mai fac N apeluri redundante -> nu mai
        # lovim rate-limit-ul T212 (429 pe /equity/portfolio, ~1 apel/5s). TTL 6s > 5s = sigur.
        self._pf_cache: tuple[float, list] | None = None
        self._pf_ttl = float(os.environ.get("T212_PORTFOLIO_TTL_SEC", "6.0"))

    def _pace(self) -> None:
        """Asigura minim _min_gap secunde intre doua apeluri T212 (peste toate threadurile)."""
        with self._lock:
            wait = self._min_gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    # -- auth / headers --------------------------------------------------------
    def _auth(self) -> str:
        if self.api_secret:
            token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
            return f"Basic {token}"
        return self.api_key

    def _headers(self) -> dict:
        # Fiecare request T212 isi cladeste antetele aici => loc bun pt throttle:
        # spatiaza orice apel, indiferent de cate threaduri folosesc clientul.
        self._pace()
        return {
            "Authorization": self._auth(),
            "User-Agent": _BROWSER_UA,  # evita 403 Cloudflare
            "Accept": "application/json",
        }

    # -- instrumente -----------------------------------------------------------
    def list_instruments(self) -> list[dict] | None:
        # Cache TTL: metadata instrumentelor se schimba rar. Evita apelul GREU
        # repetat (per activ la pornire) care declanseaza 429.
        c = getattr(self, "_instr_cache", None)
        if c and (time.monotonic() - c[0]) < 300:
            return c[1]
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
            data = json.loads(body)
            self._instr_cache = (time.monotonic(), data)
            return data
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

    def _log_read_fail(self, what: str, status: int) -> None:
        """Logheaza MOTIVUL unui read esuat (portofoliu/ordine) cu DEBOUNCE: o singura data
        la 60s per (endpoint, status). Fara asta un 429 la fiecare tick spameaza logul cu
        'indisponibil' (vezi t212_bot: ~1 din 5 ticks). Cu asta stim DE CE (429/auth/timeout)
        fara zgomot — semnal de anomalie observabil, nu 'zbor orb'."""
        now = time.monotonic()
        cache = getattr(self, "_read_fail_log", None)
        if cache is None:
            cache = self._read_fail_log = {}
        last = cache.get(what)
        if last and last[0] == status and (now - last[1]) < 60:
            return                                  # acelasi motiv, recent -> nu re-loga
        cache[what] = (status, now)
        if status == 429:
            log(f"  ! T212 {what}: rate limit (429) — indisponibil temporar")
        elif status in (401, 403):
            log(f"  ! T212 {what}: auth esuat ({status}) — verifica cheia")
        elif status == 0:
            log(f"  ! T212 {what}: timeout/retea (status 0)")
        else:
            log(f"  ! T212 {what}: HTTP {status}")

    def get_portfolio(self) -> list[dict] | None:
        """Pozitiile deschise din cont (sursa de adevar pentru reconciliere).
        Cache scurt PARTAJAT (TTL) intre threaduri -> coalesce apelurile celor N active
        intr-unul singur, sub rate-limit-ul T212 (vezi _pf_ttl in __init__)."""
        now = time.monotonic()
        with self._lock:
            c = self._pf_cache
            if c and (now - c[0]) < self._pf_ttl:
                return c[1]
        status, body = http_get(f"{self.base}/equity/portfolio", headers=self._headers())
        if status != 200 or not body:
            self._log_read_fail("portofoliu", status)
            return None
        try:
            data = json.loads(body)
        except ValueError:
            self._log_read_fail("portofoliu (JSON invalid)", status)
            return None
        with self._lock:
            self._pf_cache = (time.monotonic(), data)
        return data

    def list_active_orders(self) -> list[dict] | None:
        """Ordinele inca PENDING (cele executate dispar de aici -> apar in portofoliu)."""
        status, body = http_get(f"{self.base}/equity/orders", headers=self._headers())
        if status != 200 or not body:
            self._log_read_fail("ordine active", status)
            return None
        try:
            return json.loads(body)
        except ValueError:
            self._log_read_fail("ordine active (JSON invalid)", status)
            return None

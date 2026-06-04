"""
Binance WebSocket — infrastructură comună + două stream-uri
===========================================================

BinanceWSBase
    Nucleul comun (thread + asyncio loop, reconnect cu backoff, stop curat,
    sleep întreruptibil). Subclasele implementează doar `_connect_and_run`.

BinanceMarketStream       (public, market data)
    Combined Stream: 1 WS · 1 thread · N simboluri (ticker), subscribe dinamic.
    (alias vechi: BinanceWebSocketManager)
    URL: wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/...

BinanceUserDataStream     (privat, user-data — execution reports)
    WS-API cu session.logon (Ed25519) + keepalive; livrează evenimentele prin
    callback-uri (on_event/on_available/on_healthy/on_unhealthy), deci nu
    depinde de cacheManager (fără import circular).
"""

import asyncio
import json
import logging
import threading
import time
import websockets

from typing import Dict, Optional, Set, Callable

import symbols as sym
import utils as u
from keys.apikeys import api_key_ws

logger = logging.getLogger("binance.ws")

# ─── Constante ────────────────────────────────────────────────────────────────

WS_BASE_URL      = "wss://stream.binance.com:9443/stream"   # market data
WS_API_URL       = "wss://ws-api.binance.com:443/ws-api/v3"  # user-data (auth)
WS_MAX_STREAMS   = 1024        # limita Binance per conexiune
WS_RECV_TIMEOUT  = 3.0         # TREBUIE < WS_STOP_TIMEOUT (market)
WS_USERDATA_RECV_TIMEOUT = 30.0
WS_STOP_TIMEOUT  = 8.0
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT  = 10
WS_CLOSE_TIMEOUT = 2
WS_RETRY_INITIAL = 1.0
WS_RETRY_MAX     = 60.0
WS_USERDATA_KEEPALIVE_SEC = 30 * 60
WS_USERDATA_LOSS_TIMEOUT_SEC = 40


class _Cmd:
    SUBSCRIBE   = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"


# ══════════════════════════════════════════════════════════════════════════════
#  Bază comună: thread + asyncio + reconnect cu backoff + stop curat
# ══════════════════════════════════════════════════════════════════════════════

class BinanceWSBase:
    # Sesiune considerată „stabilă" → resetăm backoff-ul doar dacă a rezistat atât.
    # Previne reconnect-storm (login repetat) care ar putea lovi connection rate-limit-ul.
    STABLE_SESSION_SEC = 60.0

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._retry_delay = WS_RETRY_INITIAL
        self._start_lock = threading.Lock()

    # ─── Ciclu de viață thread ────────────────────────────────────────────────
    def start(self, name: str = "BinanceWS", daemon: bool = True) -> "BinanceWSBase":
        with self._start_lock:          # [F2] apeluri concurente de start() sunt safe
            if self.is_running:
                logger.info("%s already running", name)
                return self
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._thread_worker, name=name, daemon=daemon)
            self._thread.start()
        logger.info("%s started", name)
        return self

    def stop(self, timeout: float = WS_STOP_TIMEOUT) -> bool:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("WS thread did not stop within %.1fs", timeout)
                return False
        return True

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _thread_worker(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:
            if not self._is_shutdown_error(e):
                logger.exception("WS thread crashed: %s", e)
        finally:
            logger.info("WS thread exited")

    # ─── Buclă generică de reconnect ──────────────────────────────────────────
    async def _main(self) -> None:
        """Default: reconnect peste _connect_and_run. Subclasele pot suprascrie
        dacă au nevoie de setup (ex. crearea cozii async în loop-ul corect)."""
        await self._run_with_reconnect(self._connect_and_run)

    async def _run_with_reconnect(self, body: Callable) -> None:
        # Reconectare cu backoff. Cheie anti rate-limit:
        #  - între ORICE două încercări dormim _retry_delay (chiar și la disconnect
        #    „curat", fără excepție) → fără storm de reconectări instant;
        #  - backoff-ul se resetează DOAR după o sesiune stabilă (>= STABLE_SESSION_SEC),
        #    nu la fiecare conectare → flapping-ul crește delay-ul.
        while not self._stop_event.is_set():
            t0 = time.time()
            exc = None
            try:
                await body()                          # întoarce/ridică la sfârșitul sesiunii
            except Exception as e:
                exc = e
                if self._is_shutdown_error(e):
                    break
                logger.warning("WS error: %s", e)
            self._on_session_end(exc)
            if self._stop_event.is_set():
                break
            if time.time() - t0 >= self.STABLE_SESSION_SEC:
                self._reset_backoff()                 # sesiune stabilă → reluăm de la delay mic
            logger.info("WS reconnect în %.1fs", self._retry_delay)
            await self._interruptible_sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, WS_RETRY_MAX)
        logger.info("WS reconnect loop stopped")

    def _reset_backoff(self) -> None:
        self._retry_delay = WS_RETRY_INITIAL

    def _on_session_end(self, exc: Optional[Exception]) -> None:
        """Hook la sfârșitul unei sesiuni (exc=None dacă s-a încheiat curat).
        Subclasele pot reacționa (ex. user-data marchează WS unhealthy)."""
        pass

    async def _connect_and_run(self) -> None:
        """O conexiune completă (subclasa implementează). Ridică la disconnect."""
        raise NotImplementedError

    # ─── Helpers comune ───────────────────────────────────────────────────────
    async def _interruptible_sleep(self, delay: float, step: float = 0.2) -> None:
        elapsed = 0.0
        while elapsed < delay and not self._stop_event.is_set():
            await asyncio.sleep(min(step, delay - elapsed))
            elapsed += step

    @staticmethod
    def _is_shutdown_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, RuntimeError) and (
            "cannot schedule new futures after shutdown" in msg
            or "event loop is closed" in msg
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Market data — Combined Stream (public)
# ══════════════════════════════════════════════════════════════════════════════

class BinanceMarketStream(BinanceWSBase):
    """
    Stream de MARKET DATA (public): preturi live via Combined Stream — 1 thread,
    1 WS, N simboluri multiplexate. Simetric cu BinanceUserDataStream.

    Public API:
        start()/stop(), add_symbol(s)/remove_symbol(s), get_price(s),
        get_all_prices(), subscribe(sub)/unsubscribe(sub), running_symbols.
    """

    def __init__(self, symbols: Optional[list] = None):
        super().__init__()
        self._prices: Dict[str, float] = {}
        self._subscribed: Set[str]     = set()
        self._lock = threading.Lock()
        self._cmd_queue: Optional[asyncio.Queue] = None
        self._req_id = 0
        self._subscribers = []
        if symbols:
            for s in symbols:
                self._subscribed.add(s.upper())

    def start(self, name: str = "BinanceWS", daemon: bool = False) -> "BinanceMarketStream":
        # market manager rulează ca thread NON-daemon (ca varianta originală)
        super().start(name=name, daemon=daemon)
        return self

    # ─── Subscription management (observeri) ──────────────────────────────────
    def subscribe(self, subscriber) -> None:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _notify_subscribers(self, symbol: str, items) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.on_items_update(symbol, items)
            except Exception as e:
                logger.error("Subscriber notify error: %s", e)

    # ─── Helpers ──────────────────────────────────────────────────────────────
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _stream_name(self, symbol: str) -> str:
        return f"{symbol.lower()}@ticker"

    def _build_url(self, symbols: Set[str]) -> str:
        streams = "/".join(self._stream_name(s) for s in sorted(symbols))
        return f"{WS_BASE_URL}?streams={streams}"

    # ─── Async core ───────────────────────────────────────────────────────────
    async def _main(self) -> None:
        self._cmd_queue = asyncio.Queue()   # trebuie creat în loop-ul în care e folosit
        await self._run_with_reconnect(self._connect_and_run)

    async def _connect_and_run(self) -> None:
        # Așteptarea de simboluri se face AICI (nu ca „sesiune") ca să nu penalizeze
        # backoff-ul: _connect_and_run întoarce doar după o sesiune reală de conexiune.
        while not self._stop_event.is_set():
            with self._lock:
                current_symbols = set(self._subscribed)
            if current_symbols:
                break
            await self._interruptible_sleep(1.0)
        if self._stop_event.is_set() or not current_symbols:
            return
        url = self._build_url(current_symbols)
        logger.info("Connecting to combined stream (%d symbols)...", len(current_symbols))
        async with websockets.connect(
            url, ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_TIMEOUT,
            close_timeout=WS_CLOSE_TIMEOUT,
        ) as ws:
            logger.info("Connected. Streams active: %d", len(current_symbols))
            await self._session(ws)             # backoff-ul îl gestionează _run_with_reconnect

    async def _session(self, ws) -> None:
        """recv_loop (date) + cmd_loop (subscribe/unsubscribe) concurente; la
        terminarea oricăreia le anulăm pe ambele → _connect_and_run reconectează."""
        recv_task = asyncio.create_task(self._recv_loop(ws))
        cmd_task  = asyncio.create_task(self._cmd_loop(ws))
        done, pending = await asyncio.wait(
            [recv_task, cmd_task], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for task in done:
            exc = task.exception()
            if exc and not self._stop_event.is_set():
                logger.warning("Session task ended with: %s", exc)

    async def _recv_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)
                self._process_message(raw)
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection closed by server, reconnecting...")
                break
            except json.JSONDecodeError as e:
                logger.warning("JSON decode error: %s", e)
                continue

    async def _cmd_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                cmd = await asyncio.wait_for(self._cmd_queue.get(), timeout=WS_RECV_TIMEOUT)
            except asyncio.TimeoutError:
                continue
            payload = json.dumps({
                "method": cmd["method"], "params": [cmd["stream"]], "id": self._next_id()})
            try:
                await ws.send(payload)
                logger.debug("%s: %s", cmd["method"], cmd["stream"])
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection lost while sending %s for %s", cmd["method"], cmd["stream"])
                break
            except Exception as e:
                logger.error("Failed to send %s: %s", cmd["method"], e)

    def _process_message(self, raw: str) -> None:
        """{"stream": "btcusdt@ticker", "data": {"s": "BTCUSDT", "c": "65432.10", ...}}"""
        try:
            envelope = json.loads(raw)
            if "result" in envelope:                    # ack la subscribe/unsubscribe
                logger.debug("WS ack: %s", envelope)
                return
            data   = envelope.get("data", envelope)
            symbol = data["s"]
            price  = float(data["c"])
            with self._lock:
                self._prices[symbol] = price
            self._notify_subscribers(symbol, [price])
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            logger.warning("process_message error: %s | raw: %s", e, raw[:120])

    # ─── Enqueue command (thread-safe) ────────────────────────────────────────
    def _enqueue_cmd(self, method: str, symbol: str) -> None:
        if self._cmd_queue is None:
            logger.warning("Manager not started, cannot send %s for %s", method, symbol)
            return
        self._cmd_queue.put_nowait({"method": method, "stream": self._stream_name(symbol)})

    # ─── Public API ───────────────────────────────────────────────────────────
    def add_symbol(self, symbol: str) -> bool:
        symbol = symbol.upper()
        with self._lock:
            if symbol in self._subscribed:
                logger.debug("[%s] Already subscribed", symbol)
                return False
            if len(self._subscribed) >= WS_MAX_STREAMS:
                logger.warning("Max streams (%d) reached, cannot add %s", WS_MAX_STREAMS, symbol)
                return False
            self._subscribed.add(symbol)
        self._enqueue_cmd(_Cmd.SUBSCRIBE, symbol)
        return True

    def remove_symbol(self, symbol: str) -> bool:
        symbol = symbol.upper()
        with self._lock:
            if symbol not in self._subscribed:
                logger.debug("[%s] Not subscribed", symbol)
                return False
            self._subscribed.discard(symbol)
            self._prices.pop(symbol, None)
        self._enqueue_cmd(_Cmd.UNSUBSCRIBE, symbol)
        return True

    def get_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._prices.get(symbol.upper())

    def get_all_prices(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._prices)

    @property
    def running_symbols(self) -> Set[str]:
        with self._lock:
            return set(self._subscribed)


# ══════════════════════════════════════════════════════════════════════════════
#  User-data — WS-API cu session.logon (privat, execution reports)
# ══════════════════════════════════════════════════════════════════════════════

class BinanceUserDataStream(BinanceWSBase):
    """
    Stream autentificat de user-data. Livrează evenimentele prin callback-uri,
    ca să NU depindă de cacheManager (fără import circular):
        on_event(payload)      — eveniment real (executionReport etc.)
        on_available(bool)     — WS disponibil (cheie+lib prezente)
        on_healthy()           — am primit un semnal viu (event/ping)
        on_unhealthy()         — conexiune pierdută / watchdog expirat
    """

    def __init__(self, on_event: Callable,
                 on_available: Optional[Callable] = None,
                 on_healthy: Optional[Callable] = None,
                 on_unhealthy: Optional[Callable] = None,
                 keepalive_sec: float = WS_USERDATA_KEEPALIVE_SEC,
                 loss_timeout_sec: float = WS_USERDATA_LOSS_TIMEOUT_SEC):
        super().__init__()
        self.on_event = on_event
        self.on_available = on_available or (lambda *_: None)
        self.on_healthy = on_healthy or (lambda: None)
        self.on_unhealthy = on_unhealthy or (lambda: None)
        self.keepalive_sec = keepalive_sec
        self.loss_timeout_sec = loss_timeout_sec
        self._signing_key = u._load_ed25519_signing_key()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._health_lock = threading.Lock()
        self._last_event_ts = 0.0
        self._available = False
        self._healthy = False

    # ─── stare health locală + propagare prin callback-uri ────────────────────
    def _mark_available(self, value: bool) -> None:
        with self._health_lock:
            self._available = value
        self.on_available(value)

    def _mark_event(self) -> None:
        with self._health_lock:
            self._last_event_ts = time.time()
            self._healthy = True
        self.on_healthy()

    def _mark_unhealthy(self) -> None:
        with self._health_lock:
            self._healthy = False
        self.on_unhealthy()

    def start(self, name: str = "WSUserData", daemon: bool = True) -> "BinanceUserDataStream":
        super().start(name=name, daemon=daemon)
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="WSUserDataWatchdog", daemon=True)
            self._watchdog_thread.start()
        return self

    def stop(self, timeout: float = WS_STOP_TIMEOUT) -> bool:
        ok = super().stop(timeout=timeout)              # setează _stop_event + join run-thread
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=timeout)  # oprire curată și a watchdog-ului
        return ok

    # ─── logon semnat (login + keepalive) ─────────────────────────────────────
    def _signed_logon_msg(self, msg_id: str) -> str:
        timestamp = int(time.time() * 1000)
        params_str = f"apiKey={api_key_ws}&timestamp={timestamp}"
        signature = u._sign_ed25519(self._signing_key, params_str)
        return json.dumps({
            "id": msg_id, "method": "session.logon",
            "params": {"apiKey": api_key_ws, "timestamp": timestamp, "signature": signature}})

    @staticmethod
    def _classify(event: dict):
        """(kind, payload): 'ping' / 'response' (comandă) / 'event' (despachetat)."""
        if "id" in event:
            return ("ping" if event.get("id") == "ping" else "response"), event
        if "event" in event:
            return "event", event["event"]
        return "event", event

    async def _main(self) -> None:
        if self._signing_key is None:
            self._mark_available(False); self._mark_unhealthy()
            logger.error("[WS] Cheia Ed25519 lipsește → fallback polling.")
            return
        self._mark_available(True)
        await self._run_with_reconnect(self._connect_and_run)

    async def _connect_and_run(self) -> None:
        async with websockets.connect(
            WS_API_URL, ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_INTERVAL) as ws:
            # Login
            await ws.send(self._signed_logon_msg("login"))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if resp.get("status") != 200:
                raise RuntimeError(f"login eșuat: {resp}")     # → backoff în bază
            logger.info("[WS] ✅ Login OK")
            # NU resetăm backoff aici: reset-ul se face în _run_with_reconnect doar
            # dacă sesiunea rezistă (anti reconnect-storm → connection rate-limit).

            await ws.send(json.dumps({"id": "sub", "method": "userDataStream.subscribe"}))
            self._mark_event()
            last_keepalive = last_ping = time.time()

            while not self._stop_event.is_set():
                now = time.time()
                if now - last_keepalive >= self.keepalive_sec:
                    await ws.send(self._signed_logon_msg("keepalive"))
                    last_keepalive = now
                if now - last_ping >= self.loss_timeout_sec / 2:
                    await ws.send(json.dumps({"id": "ping", "method": "ping"}))
                    last_ping = now

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=WS_USERDATA_RECV_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.debug("[WS] Heartbeat (no events)")
                    continue

                event = json.loads(raw)
                logger.debug("[WS RAW] %s", raw[:200])
                kind, payload = self._classify(event)
                if kind == "ping":
                    self._mark_event()
                    continue
                if kind == "response":
                    if event.get("status") not in (None, 200):
                        logger.warning("[WS] Răspuns eroare id=%s status=%s: %s",
                                       event.get("id"), event.get("status"), event)
                    continue
                self._mark_event()
                self.on_event(payload)

    def _on_session_end(self, exc: Optional[Exception]) -> None:
        # la orice cădere de sesiune (exc != None) marcăm WS unhealthy → fallback polling
        if exc is not None:
            self._mark_unhealthy()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            with self._health_lock:
                age = now - self._last_event_ts if self._last_event_ts else float("inf")
                available, healthy = self._available, self._healthy
            if available and healthy and age > self.loss_timeout_sec:
                logger.warning("[WS][WARN] Fără evenimente WS de %ds → fallback polling.", int(age))
                self._mark_unhealthy()
            self._stop_event.wait(5)


# Alias de compatibilitate (cod/teste vechi care folosesc numele generic anterior).
BinanceWebSocketManager = BinanceMarketStream


# ─── Entry point market data (singleton partajat, start LAZY) ──────────────────

bapi_ws_manager = BinanceMarketStream(symbols=sym.symbols)   # fără socket la import


def get_ws_manager() -> BinanceMarketStream:
    """Întoarce stream-ul de market data, pornindu-l la prima cerere (start lazy)."""
    if not bapi_ws_manager.is_running:
        bapi_ws_manager.start()
    return bapi_ws_manager


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
    manager = get_ws_manager()
    try:
        while True:
            time.sleep(5)
            prices = manager.get_all_prices()
            print(f"Prices received: {len(prices)} / {len(manager.running_symbols)}")
            for symbol, price in list(prices.items())[:3]:
                print(f"  {symbol}: {price:.4f}")
    except KeyboardInterrupt:
        print("Shutting down...")
        manager.stop()

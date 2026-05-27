"""
BinanceWebSocketManager — Combined Stream Architecture
=======================================================

1 WebSocket  ·  1 thread  ·  1 event loop  ·  N simboluri

URL:  wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/...
      (max 1024 streams per conexiune — limita Binance)

Mesaj primit:
    {"stream": "btcusdt@ticker", "data": {"s": "BTCUSDT", "c": "65432.10", ...}}

Subscribe/unsubscribe dinamice via WebSocket protocol messages —
fara reconectare, fara thread nou.
"""

import asyncio
import json
import logging
import threading
import time
import websockets

from typing import Dict, Optional, Set
#ogger = logging.getLogger(__name__)

import symbols as sym

# ─── Constante ────────────────────────────────────────────────────────────────

WS_BASE_URL      = "wss://stream.binance.com:9443/stream"
WS_MAX_STREAMS   = 1024        # limita Binance per conexiune
WS_RECV_TIMEOUT  = 3.0         # TREBUIE < WS_STOP_TIMEOUT
WS_STOP_TIMEOUT  = 8.0
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT  = 10
WS_CLOSE_TIMEOUT = 2
WS_RETRY_INITIAL = 1.0
WS_RETRY_MAX     = 60.0


class _Cmd:
    SUBSCRIBE   = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"


class BinanceWebSocketManager:
    """
    Manager pentru preturi live Binance via Combined Stream.

    Arhitectura:
        - 1 thread OS (non-daemon)
        - 1 asyncio event loop (asyncio.run in thread)
        - 1 WebSocket TCP connection
        - N simboluri multiplexate pe aceeasi conexiune

    Public API:
        start()                   — porneste thread-ul si se conecteaza
        stop()                    — shutdown graceful, join thread
        add_symbol(symbol)        — subscribe simbol nou (fara reconectare)
        remove_symbol(symbol)     — unsubscribe simbol (fara reconectare)
        get_price(symbol)         — thread-safe getter
        get_all_prices()          — snapshot complet al preturilor
        running_symbols           — set de simboluri active curent
    """

    def __init__(self, symbols: Optional[list] = None):
        self._prices: Dict[str, float] = {}
        self._subscribed: Set[str]     = set()
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._cmd_queue: Optional[asyncio.Queue] = None
        self._thread: Optional[threading.Thread] = None
        self._req_id = 0

        self._subscribers = []

        if symbols:
            for s in symbols:
                self._subscribed.add(s.upper())

    #-─ Subscription management ─────────────────────────────────────────────────
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
                print(f"Subscriber notify error: {e}")
                    
    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _stream_name(self, symbol: str) -> str:
        return f"{symbol.lower()}@ticker"

    def _build_url(self, symbols: Set[str]) -> str:
        streams = "/".join(self._stream_name(s) for s in sorted(symbols))
        return f"{WS_BASE_URL}?streams={streams}"

    @staticmethod
    def _is_shutdown_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, RuntimeError) and (
            "cannot schedule new futures after shutdown" in msg
            or "event loop is closed" in msg
        )

    # ─── Thread entry point ───────────────────────────────────────────────────

    def _thread_worker(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as e:
            if not self._is_shutdown_error(e):
                print(f"Thread worker crashed: {e}", exc_info=True)
        finally:
            print("WebSocket thread exited")

    # ─── Async core ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """asyncio.Queue trebuie creat in acelasi loop in care e folosit."""
        self._cmd_queue = asyncio.Queue()
        await self._connect_with_retry()

    async def _connect_with_retry(self) -> None:
        retry_delay = WS_RETRY_INITIAL

        while not self._stop_event.is_set():
            with self._lock:
                current_symbols = set(self._subscribed)

            if not current_symbols:
                print("No symbols to subscribe, waiting...")
                await self._interruptible_sleep(1.0)
                continue

            url = self._build_url(current_symbols)

            try:
                print(f"Connecting to combined stream ({len(current_symbols)} symbols)...")
                async with websockets.connect(
                    url,
                    ping_interval=WS_PING_INTERVAL,
                    ping_timeout=WS_PING_TIMEOUT,
                    close_timeout=WS_CLOSE_TIMEOUT,
                ) as ws:
                    print(f"Connected. Streams active: {len(current_symbols)}")
                    retry_delay = WS_RETRY_INITIAL

                    await self._session(ws)

            except websockets.exceptions.WebSocketException as e:
                if self._stop_event.is_set():
                    break
                print(f"WebSocket error: {e}. Retry in {retry_delay}s")
                await self._interruptible_sleep(retry_delay)
                retry_delay = min(retry_delay * 2, WS_RETRY_MAX)

            except (RuntimeError, OSError) as e:
                if self._stop_event.is_set() or self._is_shutdown_error(e):
                    break
                print(f"Connection error: {e}. Retry in {retry_delay}s")
                await self._interruptible_sleep(retry_delay)
                retry_delay = min(retry_delay * 2, WS_RETRY_MAX)

            except Exception as e:
                if self._stop_event.is_set() or self._is_shutdown_error(e):
                    break
                print(f"Unexpected error: {e}. Retry in {retry_delay}s", exc_info=True)
                await self._interruptible_sleep(retry_delay)
                retry_delay = min(retry_delay * 2, WS_RETRY_MAX)

        print("Connect loop stopped")

    async def _session(self, ws) -> None:
        """
        Doua coroutine concurente pe aceeasi conexiune:
          _recv_loop  — citeste mesaje de date
          _cmd_loop   — trimite subscribe/unsubscribe
        La disconnect oricareia, le anulam pe amandoua si _connect_with_retry
        face reconnect (cu re-subscribe automat via URL).
        """
        recv_task = asyncio.create_task(self._recv_loop(ws))
        cmd_task  = asyncio.create_task(self._cmd_loop(ws))

        done, pending = await asyncio.wait(
            [recv_task, cmd_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        for task in done:
            exc = task.exception()
            if exc and not self._stop_event.is_set():
                print(f"Session task ended with: {exc}")

    async def _recv_loop(self, ws) -> None:
        """Citeste si proceseaza mesaje de date de la Binance."""
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)
                self._process_message(raw)

            except asyncio.TimeoutError:
                continue  # normal — stop_event check si continua

            except websockets.exceptions.ConnectionClosed:
                print("Connection closed by server, reconnecting...")
                break

            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                continue

    async def _cmd_loop(self, ws) -> None:
        """
        Consuma comenzi din queue si le trimite la Binance.
        La reconectare _connect_with_retry reconstruieste URL-ul cu
        self._subscribed curent, deci re-subscribe e automat.
        _cmd_loop trimite doar schimbarile dinamice (add/remove in timpul sesiunii).
        """
        while not self._stop_event.is_set():
            try:
                cmd = await asyncio.wait_for(
                    self._cmd_queue.get(),
                    timeout=WS_RECV_TIMEOUT,
                )
            except asyncio.TimeoutError:
                continue

            payload = json.dumps({
                "method": cmd["method"],
                "params": [cmd["stream"]],
                "id":     self._next_id(),
            })

            try:
                await ws.send(payload)
                print(f"{cmd['method']}: {cmd['stream']}")
            except websockets.exceptions.ConnectionClosed:
                print(f"Connection lost while sending {cmd['method']} for {cmd['stream']}")
                break
            except Exception as e:
                print(f"Failed to send {cmd['method']}: {e}")

    # ─── Process message ──────────────────────────────────────────────────────

    def _process_message(self, raw: str) -> None:
        """
        Combined stream format:
        {"stream": "btcusdt@ticker", "data": {"s": "BTCUSDT", "c": "65432.10", ...}}
        """
        try:
            envelope = json.loads(raw)

            # Raspunsuri la subscribe/unsubscribe ({"result": null, "id": N})
            if "result" in envelope:
                print(f"WS ack: {envelope}")
                return

            data   = envelope.get("data", envelope)
            symbol = data["s"]
            price  = float(data["c"])

            with self._lock:
                self._prices[symbol] = price
            
            self._notify_subscribers(symbol, [price])

        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"process_message error: {e} | raw: {raw[:120]}")

    # ─── Sleep interruptibil ──────────────────────────────────────────────────

    async def _interruptible_sleep(self, delay: float, step: float = 0.2) -> None:
        elapsed = 0.0
        while elapsed < delay and not self._stop_event.is_set():
            await asyncio.sleep(min(step, delay - elapsed))
            elapsed += step

    # ─── Enqueue command (thread-safe) ────────────────────────────────────────

    def _enqueue_cmd(self, method: str, symbol: str) -> None:
        """
        Queue.put_nowait e safe din orice thread in CPython.
        Comanda e procesata async de _cmd_loop in thread-ul WS.
        """
        if self._cmd_queue is None:
            print(f"Manager not started, cannot send {method} for {symbol}")
            return
        self._cmd_queue.put_nowait({
            "method": method,
            "stream": self._stream_name(symbol),
        })

    # ─── Public API ───────────────────────────────────────────────────────────

    def start(self) -> "BinanceWebSocketManager":
        """Porneste thread-ul WS. Returneaza self pentru chaining."""
        if self._thread and self._thread.is_alive():
            print("Already running")
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_worker,
            name="BinanceWS",
            daemon=False,
        )
        self._thread.start()
        print("WebSocket manager started")
        return self

    def stop(self, timeout: float = WS_STOP_TIMEOUT) -> bool:
        """
        Shutdown graceful. Returneaza True daca thread-ul s-a oprit curat.
        """
        print("Stopping WebSocket manager...")
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                print(f"Thread did not stop within {timeout}s")
                return False

        print("WebSocket manager stopped")
        return True

    def add_symbol(self, symbol: str) -> bool:
        """
        Subscribe un simbol nou fara reconectare.
        Returneaza False daca simbolul era deja activ sau limita e atinsa.
        """
        symbol = symbol.upper()

        with self._lock:
            if symbol in self._subscribed:
                print(f"[{symbol}] Already subscribed")
                return False
            if len(self._subscribed) >= WS_MAX_STREAMS:
                print(f"Max streams ({WS_MAX_STREAMS}) reached, cannot add {symbol}")
                return False
            self._subscribed.add(symbol)

        self._enqueue_cmd(_Cmd.SUBSCRIBE, symbol)
        return True

    def remove_symbol(self, symbol: str) -> bool:
        """
        Unsubscribe un simbol fara a afecta restul.
        Returneaza False daca simbolul nu era activ.
        """
        symbol = symbol.upper()

        with self._lock:
            if symbol not in self._subscribed:
                print(f"[{symbol}] Not subscribed")
                return False
            self._subscribed.discard(symbol)
            self._prices.pop(symbol, None)

        self._enqueue_cmd(_Cmd.UNSUBSCRIBE, symbol)
        return True

    def get_price(self, symbol: str) -> Optional[float]:
        """Thread-safe. None daca simbolul nu e known inca."""
        with self._lock:
            return self._prices.get(symbol.upper())

    def get_all_prices(self) -> Dict[str, float]:
        """Snapshot thread-safe al tuturor preturilor curente."""
        with self._lock:
            return dict(self._prices)

    @property
    def running_symbols(self) -> Set[str]:
        with self._lock:
            return set(self._subscribed)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ─── Entry point ──────────────────────────────────────────────────────────────

bapi_ws_manager = BinanceWebSocketManager(symbols=sym.symbols)
bapi_ws_manager.start()

if __name__ == "__main__":
    import symbols as sym

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s",
    )

    manager = BinanceWebSocketManager(symbols=sym.symbols)
    manager.start()

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
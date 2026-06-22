#!/usr/bin/env python3
"""
kraken_cachemanager.py — cache PARTAJAT cross-proces de fills Kraken (HYPE multi-proces).

DE CE: 2-3 procese tranzactioneaza HYPE pe ACELASI cont Kraken. Daca fiecare isi citeste
singur TradesHistory: (1) gardul de profit e ORB cross-proces (procesul B nu vede sell-ul
tocmai pus de A pana-i expira cache-ul TTL) -> ar putea cumpara peste sell-ul lui A; si
(2) lovesti rate-limit-ul Kraken (numarat per CHEIE/cont). Solutia (modelul cacheManager
Binance): UN proces tine fills-urile intr-un FISIER COMUN; toate procesele de trading
CITESC de acolo (via KrakenProvider). Asa: vedere comuna -> gard corect cross-proces +
un singur fetcher -> rate-limit minim.

Doua moduri (KRAKEN_CACHE_MODE):
  poll (DEFAULT) = poller REST (TradesHistory la POLL_INTERVAL). Decalaj ~POLL_INTERVAL.
  ws             = WebSocket `ownTrades` real-time (zero-lag) pt scalping SUB 5s.
                   Cod COMPLET, ready, dar NEACTIV implicit. Necesita `pip install
                   websocket-client`. Il pornesti cu KRAKEN_CACHE_MODE=ws in env.

Cheie: perechea DEDICATA _WS (KRAKEN_API_KEY_WS/_SECRET_WS) -> secventa de nonce proprie,
separata de procesele de trading. Fallback pe cheia default.

Format fisier = COMPATIBIL cu cache_trade.json Binance: {"items": {symbol: [trade]}, "fetchtime"}
cu trade = {symbol,id,orderId,price,qty,time,isBuyer} -> KrakenProvider il citeste cu aceeasi logica.

Ruleaza:  cd kraken && python kraken_cachemanager.py
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_dotenv, log
from kraken_client import KrakenClient

# ── config (din env, cu default-uri) ─────────────────────────────────────────
PAIRS = [p for p in os.environ.get("KRAKEN_CACHE_PAIRS", "HYPEUSD").split(",") if p]
POLL_INTERVAL = float(os.environ.get("KRAKEN_CACHE_POLL_S", "5"))
MODE = os.environ.get("KRAKEN_CACHE_MODE", "poll").strip().lower()   # poll | ws
WS_URL = "wss://ws-auth.kraken.com/"      # endpoint AUTENTIFICAT (canalul privat ownTrades)
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "cachedb", "cache_trade_kraken.json")


def _normalize(txid, tr):
    """O tranzactie din TradesHistory / ownTrades Kraken -> forma comuna (ca trade-urile Binance)."""
    return {
        "symbol": tr.get("pair"),
        "id": str(txid),
        "orderId": tr.get("ordertxid"),
        "price": tr.get("price"),
        "qty": tr.get("vol"),
        "time": int(float(tr.get("time", 0)) * 1000),   # Kraken da secunde float -> ms
        "isBuyer": (tr.get("type") == "buy"),
    }


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)            # atomic: cititorii nu vad fisier pe jumatate scris


def _fetch_rest_into(client, cache):
    """Un fetch TradesHistory -> umple cache pe symbol (folosit de poll SI de seed-ul WS)."""
    res = client._private("TradesHistory", fresh=True)   # fresh: ocoleste cache-ul TTL al clientului
    trades = (res or {}).get("trades", {}) or {}
    by_sym = {}
    for txid, tr in trades.items():
        n = _normalize(txid, tr)
        if PAIRS and "*" not in PAIRS and n["symbol"] not in PAIRS:
            continue
        by_sym.setdefault(n["symbol"], []).append(n)
    for sym, lst in by_sym.items():
        lst.sort(key=lambda t: t["time"])               # crescator -> ultimul = cel mai recent
        cache["items"][sym] = lst
        cache["fetchtime"][sym] = int(time.time() * 1000)


def poll_loop(client):
    """V1 (default): la fiecare POLL_INTERVAL ia TradesHistory, scrie fisierul."""
    cache = {"items": {}, "fetchtime": {}}
    while True:
        try:
            _fetch_rest_into(client, cache)
            _atomic_write(CACHE_FILE, cache)
            log(f"[kraken_cache][poll] {sum(len(v) for v in cache['items'].values())} fills "
                f"pt {list(cache['items'].keys())}")
        except Exception as e:
            log(f"[kraken_cache][poll] eroare: {e}")
        time.sleep(POLL_INTERVAL)


# ── WS `ownTrades` (real-time, ZERO-LAG) — COMPLET, ready, NEACTIV implicit ───
# Activare: KRAKEN_CACHE_MODE=ws. Necesita `pip install websocket-client`.
def _ws_token(client):
    """Token efemer pt WS privat (valabil ~15 min; il reiei la fiecare reconnect)."""
    return client._private("GetWebSocketsToken", fresh=True)["token"]


def ws_loop(client):
    """Real-time: subscribe ownTrades -> scrie fisierul la FIECARE fill (zero-lag).
    Seed initial din REST (sa nu pornesti cu cache gol) + reconnect automat."""
    import websocket  # lazy: doar in modul WS, ca poll-ul sa mearga fara dependenta

    cache = {"items": {}, "fetchtime": {}}
    try:
        _fetch_rest_into(client, cache)      # snapshot initial
        _atomic_write(CACHE_FILE, cache)
    except Exception as e:
        log(f"[kraken_cache][ws] seed REST esuat: {e}")

    def on_open(ws):
        try:
            ws.send(json.dumps({
                "event": "subscribe",
                "subscription": {"name": "ownTrades", "token": _ws_token(client)},
            }))
            log("[kraken_cache][ws] subscribed ownTrades")
        except Exception as e:
            log(f"[kraken_cache][ws] subscribe esuat: {e}")

    def on_message(ws, msg):
        try:
            data = json.loads(msg)
        except ValueError:
            return
        # ownTrades: [ [ {txid: {...}}, ... ], "ownTrades", {"sequence": N} ]
        if not (isinstance(data, list) and len(data) >= 2 and data[1] == "ownTrades"):
            return
        changed = False
        for entry in data[0]:
            for txid, tr in entry.items():
                n = _normalize(txid, tr)
                if PAIRS and "*" not in PAIRS and n["symbol"] not in PAIRS:
                    continue
                bucket = cache["items"].setdefault(n["symbol"], [])
                if not any(t["id"] == n["id"] for t in bucket):
                    bucket.append(n)
                    bucket.sort(key=lambda t: t["time"])
                    cache["fetchtime"][n["symbol"]] = int(time.time() * 1000)
                    changed = True
        if changed:
            _atomic_write(CACHE_FILE, cache)             # scriere INSTANT la fill -> zero-lag

    def on_error(ws, err):
        log(f"[kraken_cache][ws] error: {err}")

    while True:
        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open,
                                        on_message=on_message, on_error=on_error)
            ws.run_forever(ping_interval=20, ping_timeout=10)   # reconnect la deconectare
        except Exception as e:
            log(f"[kraken_cache][ws] run_forever: {e}")
        log("[kraken_cache][ws] deconectat; reconnect in 5s")
        time.sleep(5)


def main():
    load_dotenv(".env")
    load_dotenv("config.env")
    # Cheia DEDICATA a cachemanager-ului (perechea _WS) -> secventa de nonce PROPRIE, separata
    # de procesele de trading (KRAKEN_API_KEY). Asa nu se ciocnesc nonce-urile (Kraken cere
    # nonce strict crescator per cheie). Fallback pe cheia default daca _WS lipseste.
    key = os.environ.get("KRAKEN_API_KEY_WS") or os.environ.get("KRAKEN_API_KEY")
    secret = os.environ.get("KRAKEN_API_SECRET_WS") or os.environ.get("KRAKEN_API_SECRET")
    used_ws_key = bool(os.environ.get("KRAKEN_API_KEY_WS") and os.environ.get("KRAKEN_API_SECRET_WS"))
    if not key or not secret:
        log("[kraken_cache] FATAL: lipsesc cheile Kraken (_WS sau default) in kraken/.env"); return
    client = KrakenClient(key, secret)
    log(f"[kraken_cache] start: mode={MODE} cheie={'_WS dedicata' if used_ws_key else 'default'} "
        f"pairs={PAIRS} poll={POLL_INTERVAL}s -> {CACHE_FILE}")
    if MODE == "ws":
        ws_loop(client)        # real-time (necesita websocket-client)
    else:
        poll_loop(client)      # REST (default)


if __name__ == "__main__":
    main()

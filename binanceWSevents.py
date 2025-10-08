import json
import threading
import time
from websocket import WebSocketApp

#my imports
import log
import utils as u
import symbols as sym
import binanceapi as api


# ------------------------------
# CACHE LOCAL
# ------------------------------
balances_cache = {}
orders_cache = {}
trades_cache = []

# ------------------------------
# FUNCȚII DE MANIPULARE MESAJ
# ------------------------------
def handle_execution_report(data):
    """
    Gestionează evenimentele de tip 'executionReport' (ordine și execuții).
    """
    symbol = data["s"]
    order_id = data["i"]
    status = data["X"]
    event_type = data["x"]

    # Actualizăm ordinele în cache
    orders_cache[order_id] = {
        "symbol": symbol,
        "side": data["S"],
        "type": data["o"],
        "status": status,
        "price": float(data["p"]),
        "origQty": float(data["q"]),
        "executedQty": float(data["z"]),
        "updateTime": data["E"]
    }

    # Dacă este o execuție efectivă ("TRADE"), adăugăm în cache-ul de tranzacții
    if event_type == "TRADE":
        trade = {
            "tradeId": data["t"],
            "orderId": order_id,
            "symbol": symbol,
            "side": data["S"],
            "price": float(data["L"]),
            "qty": float(data["l"]),
            "time": data["T"]
        }
        trades_cache.append(trade)
        print(f"[TRADE] {symbol} {data['S']} {trade['qty']} @ {trade['price']}")
    else:
        print(f"[ORDER UPDATE] {symbol} → {status}")

def handle_balance_update(data):
    """
    Gestionează evenimentele de tip 'balanceUpdate'
    """
    asset = data["a"]
    delta = float(data["d"])
    timestamp = data["T"]

    balances_cache[asset] = balances_cache.get(asset, 0) + delta
    print(f"[BALANCE UPDATE] {asset}: {delta:+f} (la {timestamp})")

def handle_account_position(data):
    """
    Gestionează evenimentele de tip 'outboundAccountPosition'
    """
    for bal in data["B"]:
        asset = bal["a"]
        free = float(bal["f"])
        locked = float(bal["l"])
        balances_cache[asset] = free + locked

    print(f"[ACCOUNT UPDATE] {len(data['B'])} active actualizate")

def handle_list_status(data):
    """
    Gestionează evenimentele de tip 'listStatus' (OCO)
    """
    print(f"[OCO] {data['s']} - status: {data['l']}")

def handle_message(message, handler=None):
    """
    Router central pentru toate evenimentele WebSocket.
    """
    data = json.loads(message)
    event_type = data.get("e")

    if event_type == "executionReport":
        handle_execution_report(data)
    elif event_type == "balanceUpdate":
        handle_balance_update(data)
    elif event_type == "outboundAccountPosition":
        handle_account_position(data)
    elif event_type == "listStatus":
        handle_list_status(data)
    else:
        print(f"[ALT EVENT] {event_type} → {data}")

    if handler:
        try:
            handler(event_type, data)
        except Exception as e:
            print(f"⚠️ Eroare în handler-ul personal: {e}")

# ------------------------------
# CONECTARE WEBSOCKET
# ------------------------------
def run_ws(listen_key, external_handler=None):
    ws_url = f"wss://stream.binance.com:9443/ws/{listen_key}"

    def on_message(ws, msg):
        handle_message(msg, external_handler)

    def on_error(ws, error):
        print("⚠️ Eroare WebSocket:", error)

    def on_close(ws):
        print("🔌 Conexiune WebSocket închisă")

    def on_open(ws):
        print("✅ Conectat la Binance User Data Stream")

    ws = WebSocketApp(ws_url, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.on_open = on_open
    ws.run_forever()

# ------------------------------
# THREAD SEPARAT PENTRU WS + REÎNNOIRE LISTEN KEY
# ------------------------------
def keepalive_loop(listen_key):
    while True:
        time.sleep(30 * 60)  # la fiecare 30 minute
        try:
            api.client.keepalive_listen_key(listen_key)
            print("♻️ ListenKey reînnoit")
            # pt debug
            print(f"Orders: {len(orders_cache)}, Trades: {len(trades_cache)}, Balances: {len(balances_cache)}")
        except Exception as e:
            print("❌ Eroare la reînnoirea listenKey:", e)


_ws_started = False
def startWSevents(handler=None):
    global _ws_started
    if _ws_started:
        print("⚠️ WebSocket deja pornit! ignor apelul duplicat")
        return
    _ws_started = True

    #listen_key = api.client.new_listen_key()
    listen_key = api.client.stream_get_listen_key()

    # Thread separat pentru WS
    ws_thread = threading.Thread(target=run_ws, args=(listen_key, handler), daemon=True)
    ws_thread.start()

    # Thread pentru reîmprospătare listen key
    keepalive_thread = threading.Thread(target=keepalive_loop, args=(listen_key,), daemon=True)
    keepalive_thread.start()

    print("🚀 Ascult evenimente Binance în timp real...\n")
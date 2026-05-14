import asyncio
import json
import threading
import websockets
import time
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class BinanceWebSocketManager:
    def __init__(self):
        self.cprice: Dict[str, float] = {}
        self.stop_event = threading.Event()
        self.threads = []
        self.loops = []  # Track loops pentru cleanup
        
    def listen_to_binance(self, symbol: str):
        """Thread worker care rulează event loop-ul pentru WebSocket"""
        socket = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker"
        
        async def connect_with_retry():
            """Conexiune cu retry logic"""
            retry_delay = 1
            max_retry_delay = 60
            
            while not self.stop_event.is_set():
                try:
                    logger.info(f"Connecting to {symbol} WebSocket...")
                    async with websockets.connect(
                        socket, 
                        ping_interval=20, 
                        ping_timeout=10
                    ) as websocket:
                        logger.info(f"Connected to {symbol}")
                        retry_delay = 1  # Reset delay după conectare reușită
                        
                        while not self.stop_event.is_set():
                            try:
                                message = await asyncio.wait_for(
                                    websocket.recv(), 
                                    timeout=10
                                )
                                message_data = json.loads(message)
                                self.process_message(message_data)
                            except asyncio.TimeoutError:
                                logger.warning(f"{symbol}: No message received in 30s")
                                continue
                            except json.JSONDecodeError as e:
                                logger.error(f"{symbol}: JSON decode error: {e}")
                                continue
                                
                except websockets.exceptions.WebSocketException as e:
                    if self.stop_event.is_set():
                        break
                    logger.error(f"{symbol} WebSocket error: {e}")
                    logger.info(f"Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
                except Exception as e:
                    if self.stop_event.is_set():
                        break
                    logger.error(f"{symbol} unexpected error: {e}", exc_info=True)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
                    
            logger.info(f"{symbol} WebSocket thread stopping")
        
        # Creează și rulează event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loops.append(loop)  # Track pentru cleanup
        
        try:
            loop.run_until_complete(connect_with_retry())
        finally:
            # CRITICAL: Cleanup event loop
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            logger.info(f"{symbol} event loop closed")
    
    def process_message(self, message: dict):
        """Procesează mesajul primit de la WebSocket"""
        try:
            symbol = message['s']
            price = float(message['c'])
            self.cprice[symbol] = price
            # logger.debug(f"{symbol} price: {price:.2f}")
        except (KeyError, ValueError) as e:
            logger.error(f"Error processing message: {e}")
    
    def start_websocket(self, symbol: str) -> threading.Thread:
        """Pornește un thread pentru un simbol"""
        thread = threading.Thread(
            target=self.listen_to_binance, 
            args=(symbol,),
            name=f"WS-{symbol}",
            daemon=False  # NU daemon - vrem cleanup controlled
        )
        thread.start()
        self.threads.append(thread)
        return thread
    
    def stop_all(self, timeout: float = 5.0):
        """Oprește toate conexiunile WebSocket cu cleanup corect"""
        logger.info("Stopping all WebSocket connections...")
        self.stop_event.set()
        
        # Așteaptă ca thread-urile să se termine
        for thread in self.threads:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(f"Thread {thread.name} did not stop in time")
        
        logger.info("All WebSocket connections stopped")
    
    def get_price(self, symbol: str) -> float | None:
        """Thread-safe price getter"""
        return self.cprice.get(symbol)


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

bapi_ws_manager = BinanceWebSocketManager()

# Pornește WebSocket-uri pentru mai multe simboluri
symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
for symbol in symbols:
    bapi_ws_manager.start_websocket(symbol)

#
# price = bapi_ws_manager.get_price(symbol)
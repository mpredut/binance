
import time
import datetime
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from collections import deque

from apikeys import api_key, api_secret

# my imports

import binanceapi as api
import log
import alert
import utils as u
#import priceprediction as pp

import os

# Intervalul de timp între încercările de anulare și recreere a ordinului (în secunde)
WAIT_FOR_ORDER = 22

class TradingBot:
    def __init__(self, symbol, qty, DEFAULT_ADJUSTMENT_PERCENT):
        self.symbol = symbol
        self.qty = qty
        self.transaction_state = "COMPLETED"  # Starea inițială
        current_price = api.get_current_price(symbol)
        self.filled_buy_price = round(current_price * (1 - 0.1), 4)
        self.filled_sell_price = round(current_price * (1 + 0.1), 4)
        self.buy_filled = False
        self.sell_filled = False
        self.DEFAULT_ADJUSTMENT_PERCENT = DEFAULT_ADJUSTMENT_PERCENT

    def repetitive_buy(self, current_price, filled_sell_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT
        
        while True:
            target_buy_price = round(current_price * (1 - adjustment_percent), 4)
            print(f"[{self.symbol}] Order BUY initiated at {target_buy_price:.2f}")
            
            if self.buy_filled:
                print(f"[{self.symbol}] Ignore BUY order. It was previously filled at {self.filled_buy_price:f2}")
                return self.filled_buy_price
            
            if adjustment_percent > 0:
                buy_order = api.place_BUY_order(self.symbol, target_buy_price, self.qty)
            else:
                buy_order = api.place_safe_order("BUY", self.symbol, target_buy_price, self.qty)
            if buy_order is None:
                print("[{self.symbol}] Order BUY fail, retry ...")
                api.cancel_recent_orders(order_type, symbol, WAIT_FOR_ORDER)
                time.sleep(WAIT_FOR_ORDER)
                continue

            time.sleep(WAIT_FOR_ORDER)
            order_id = buy_order['orderId']
            self.filled_buy_price = round(float(buy_order['price']), 4)
            
            if api.check_order_filled(order_id):
                print(f"[{self.symbol}] BUY order filled at {self.filled_buy_price:.f2}")
                self.buy_filled = True
                self.sell_filled = False
                return self.filled_buy_price
                
            filled_buy_price = api.check_order_filled_by_time("BUY", symbol, time_back_in_seconds = WAIT_FOR_ORDER)
            if not filled_buy_price is None:
                print(f"[{self.symbol}] BUY order probababliy filled at {filled_buy_price:.f2}")
                self.buy_filled = True
                self.sell_filled = False
                self.filled_buy_price = filled_buy_price
                return self.filled_buy_price
                
            current_price = api.get_current_price(symbol)
            if current_price > filled_sell_price:
                print(f"[{self.symbol}] Bed day :-(. try buy at current price {current_price}")
                adjustment_percent = 0
            else:
                adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT

            if not api.cancel_order(symbol, order_id):
                print(f"[{self.symbol}] Cancel SELL order failed. may be was filled :-)? Continue...")
                return self.filled_buy_price


    def repetitive_sell(self, current_price, filled_buy_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT

        while True:
            target_sell_price = round(current_price * (1 + adjustment_percent), 4)
            print(f"[{self.symbol}] Order SELL initiated at {target_sell_price:.2f}")

            if self.sell_filled:
                print(f"[{self.symbol}] Ignore SELL order. It was previously filled at {self.filled_sell_price:.2f}")
                return self.filled_sell_price

            
            if adjustment_percent > 0:
                sell_order = api.place_SELL_order(self.symbol, target_sell_price, self.qty)
            else:
                sell_order = api.place_safe_order("SELL", self.symbol, target_sell_price, self.qty)
            if sell_order is None:
                print(f"[{self.symbol}] Order SELL failed, retrying...")
                api.cancel_recent_orders(order_type, symbol, WAIT_FOR_ORDER)
                time.sleep(WAIT_FOR_ORDER)
                continue

            time.sleep(WAIT_FOR_ORDER)
            order_id = sell_order['orderId']
            self.filled_sell_price = round(float(sell_order['price']), 4)

            if api.check_order_filled(order_id):
                print(f"[{self.symbol}] SELL order filled at {self.filled_sell_price:.2f}")
                self.buy_filled = False
                self.sell_filled = True
                return self.filled_sell_price
                
            filled_sell_price = api.check_order_filled_by_time("SELL", symbol, time_back_in_seconds = WAIT_FOR_ORDER)
            if not filled_sell_price is None:
                print(f"[{self.symbol}] SELL order probababliy filled at {filled_sell_price:.f2}")
                self.buy_filled = True
                self.sell_filled = False
                self.filled_sell_price = filled_sell_price
                return self.filled_sell_price
                
            current_price = api.get_current_price(self.symbol)
            if current_price < filled_buy_price:
                print(f"[{self.symbol}] Bed day :-(. try SELL at current price {current_price:.2f}")
                adjustment_percent = 0
            else:
                adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT

            if not api.cancel_order(self.symbol, order_id):
                print(f"[{self.symbol}] Cancel SELL order failed. may be was filled :-)? Continue...")
                return self.filled_sell_price

    def run(self):
        while True:
            try:
                current_price = api.get_current_price(self.symbol)
                print(f"[{self.symbol}] Current price: {current_price:.2f}")

                filled_sell_price = self.repetitive_sell(current_price, self.filled_buy_price)
                current_price = api.get_current_price(self.symbol)
                filled_buy_price = self.repetitive_buy(current_price, filled_sell_price)

                print(f"[{self.symbol}] Transaction complete: Bought at {filled_buy_price:.2f}, Sold at {filled_sell_price:.2f}")
                if filled_buy_price < filled_sell_price:
                    print(f"[{self.symbol}] PROFIT: Profit ratio {filled_sell_price / filled_buy_price:.2f}")
                else:
                    print(f"[{self.symbol}] LOSS: Loss ratio {filled_sell_price / filled_buy_price:.2f}")

                time.sleep(1)

                if self.buy_filled == self.sell_filled:
                    self.buy_filled = not self.sell_filled
            except Exception as e:
                print(f"[{self.symbol}] Unexpected error: {e}")
                if self.buy_filled == self.sell_filled:
                    self.buy_filled = not self.sell_filled
                time.sleep(1)


DEFAULT_ADJUSTMENT_PERCENT = round(u.calculate_difference_percent(60000, 60000 - 310) / 100, 4)
print(f"[INFO] DEFAULT_ADJUSTMENT_PERCENT = {DEFAULT_ADJUSTMENT_PERCENT}")

symbol = api.symbol
bot = TradingBot(symbol, 0.017, DEFAULT_ADJUSTMENT_PERCENT=DEFAULT_ADJUSTMENT_PERCENT)
bot.run()

    

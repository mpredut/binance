"""A manual simulation; it is not part of the live runtime."""

import random
import time
import matplotlib.pyplot as plt
from collections import deque

class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-5):
        self.window_size = window_size
        self.prices = deque()  # Keeps every price in the window.
        self.min_deque = deque()  # Gestionarea minimului
        self.max_deque = deque()  # Gestionarea maximului
        self.current_index = 0  # An internal counter tracking the index.
        self.max_index = max_index  # The threshold at which normalisation happens.
        self.epsilon = epsilon  # The tolerance for approximately equal lows.

    def normalize_indices(self):
        """Normalise the indices when max_index is reached."""
        min_index = self.min_deque[0][0] if self.min_deque else 0
        self.min_deque = deque([(index - min_index, price) for index, price in self.min_deque])
        self.max_deque = deque([(index - min_index, price) for index, price in self.max_deque])
        self.current_index -= min_index  # Ajustam indexul curent

    def process_price(self, price):
        # Add the new price to the price list.
        self.prices.append(price)

        # Remove the prices that fall out of the window.
        if len(self.prices) > self.window_size:
            self.prices.popleft()

        # Handling of the current low and high.
        self._manage_minimum(price)
        self._manage_maximum(price)

        # Incrementam indexul intern
        self.current_index += 1

    def _manage_minimum(self, price):
        """Handling of the current low in the window."""
        # Normalise the indices if max_index is reached.
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Remove the elements that are outside the window (too old).
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            self.min_deque.popleft()

        # Check whether the current price is approximately equal to any price already in `min_deque`.
        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon:
                return  # Do not add the current price if an equivalent already exists.
        
        # Remove the trailing elements that are larger than the current price.
        while self.min_deque and self.min_deque[-1][1] > price:
            self.min_deque.pop()

        # Add the current price.
        self.min_deque.append((self.current_index, price))

    def _manage_maximum(self, price):
        """Handling of the current high in the window."""
        # Normalise the indices if max_index is reached.
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Remove the elements that are outside the window (too old).
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            self.max_deque.popleft()

        # Remove the trailing elements that are smaller than the current price (to keep the latest high).
        while self.max_deque and self.max_deque[-1][1] <= price:
            self.max_deque.pop()

        # Add the current price.
        self.max_deque.append((self.current_index, price))

    def get_min(self):
        """Return the current low in the window and its relative position."""
        if not self.min_deque:
            return None, None
        min_index, min_price = self.min_deque[0]
        relative_position = min_index - (self.current_index - len(self.prices))
        return min_price, relative_position

    def get_max(self):
        """Return the current high in the window and its relative position."""
        if not self.max_deque:
            return None, None
        max_index, max_price = self.max_deque[0]
        relative_position = max_index - (self.current_index - len(self.prices))
        return max_price, relative_position

    def get_prices(self):
        """Return every current price in the window."""
        return list(self.prices)

def plot_graphs(price_window, full_prices):
    """Plot the charts with every price and the current window."""
    # Curatam graficele anterioare
    plt.clf()

    # Plotam graficul complet
    plt.subplot(2, 1, 1)
    plt.plot(full_prices, marker='o', linestyle='-', label='All Prices')
    plt.title('All Prices (Last 500)')
    plt.xlabel('Index')
    plt.ylabel('Price')
    plt.grid(True)

    # Plotam graficul ferestrei glisante
    plt.subplot(2, 1, 2)
    prices = price_window.get_prices()
    min_price, min_pos = price_window.get_min()
    max_price, max_pos = price_window.get_max()

    plt.plot(prices, marker='o', linestyle='-', label='Window Prices')
    
    if min_price is not None:
        plt.plot(min_pos, min_price, 'ro', label=f'Min: {min_price}')
    
    if max_price is not None:
        plt.plot(max_pos, max_price, 'go', label=f'Max: {max_price}')
    
    plt.title('Price Window with Min and Max')
    plt.xlabel('Relative Position in Window')
    plt.ylabel('Price')
    plt.grid(True)
    
    # Actualizam graficele
    plt.tight_layout()
    plt.pause(0.1)

# The real-time simulation with random values plus the full view and the window.
window_size = 10
max_full_prices = 500
price_window = PriceWindow(window_size)
full_prices = deque(maxlen=max_full_prices)  # We keep up to 500 values.

plt.ion()  # Enable interactive mode so the charts can be watched in real time.

# The simulation loop with an endless price stream.
try:
    while True:
        price = random.uniform(1, 100)
        full_prices.append(price)  # Add the price to the full chart.
        price_window.process_price(price)
        plot_graphs(price_window, full_prices)
        time.sleep(1.2)  # A small delay to simulate real time.
except KeyboardInterrupt:
    print("Simulare oprita manual.")

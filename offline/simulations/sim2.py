"""Simulare manuala; nu face parte din runtime-ul live."""

import random
import time
import matplotlib.pyplot as plt
from collections import deque

class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-5):
        self.window_size = window_size
        self.prices = deque()  # Pastreaza toate preturile din fereastra
        self.min_deque = deque()  # Gestionarea minimului
        self.max_deque = deque()  # Gestionarea maximului
        self.current_index = 0  # Contor intern pentru a urmari indexul
        self.max_index = max_index  # Pragul la care se face normalizarea
        self.epsilon = epsilon  # Toleranta pentru minimurile aproximativ egale

    def normalize_indices(self):
        """Normalizare indicilor cand se atinge max_index."""
        min_index = self.min_deque[0][0] if self.min_deque else 0
        self.min_deque = deque([(index - min_index, price) for index, price in self.min_deque])
        self.max_deque = deque([(index - min_index, price) for index, price in self.max_deque])
        self.current_index -= min_index  # Ajustam indexul curent

    def process_price(self, price):
        # Adaugam noul pret la lista de preturi
        self.prices.append(price)

        # Eliminam preturile care ies din fereastra
        if len(self.prices) > self.window_size:
            self.prices.popleft()

        # Gestionarea minimului si maximului curent
        self._manage_minimum(price)
        self._manage_maximum(price)

        # Incrementam indexul intern
        self.current_index += 1

    def _manage_minimum(self, price):
        """Gestionarea minimului curent din fereastra."""
        # Normalizam indicii daca atingem max_index
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Eliminam elementele care sunt in afara ferestrei (prea vechi)
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            self.min_deque.popleft()

        # Verificam daca pretul curent este aproximativ egal cu oricare pret existent in `min_deque`
        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon:
                return  # Nu adaugam pretul curent daca exista deja un echivalent
        
        # Eliminam elementele din spate mai mari decat pretul curent
        while self.min_deque and self.min_deque[-1][1] > price:
            self.min_deque.pop()

        # Adaugam pretul curent
        self.min_deque.append((self.current_index, price))

    def _manage_maximum(self, price):
        """Gestionarea maximului curent din fereastra."""
        # Normalizam indicii daca atingem max_index
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Eliminam elementele care sunt in afara ferestrei (prea vechi)
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            self.max_deque.popleft()

        # Eliminam elementele din spate mai mici decat pretul curent (pentru a pastra ultimul maxim)
        while self.max_deque and self.max_deque[-1][1] <= price:
            self.max_deque.pop()

        # Adaugam pretul curent
        self.max_deque.append((self.current_index, price))

    def get_min(self):
        """Returneaza minimul curent din fereastra si pozitia relativa."""
        if not self.min_deque:
            return None, None
        min_index, min_price = self.min_deque[0]
        relative_position = min_index - (self.current_index - len(self.prices))
        return min_price, relative_position

    def get_max(self):
        """Returneaza maximul curent din fereastra si pozitia relativa."""
        if not self.max_deque:
            return None, None
        max_index, max_price = self.max_deque[0]
        relative_position = max_index - (self.current_index - len(self.prices))
        return max_price, relative_position

    def get_prices(self):
        """Returneaza toate preturile curente din fereastra."""
        return list(self.prices)

def plot_graphs(price_window, full_prices):
    """Ploteaza graficele cu toate preturile si fereastra curenta."""
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

# Simularea in timp real cu valori aleatorii si vizualizarea completa + fereastra
window_size = 10
max_full_prices = 500
price_window = PriceWindow(window_size)
full_prices = deque(maxlen=max_full_prices)  # Pastram pana la 500 de valori

plt.ion()  # Activam modul interactiv pentru a vizualiza graficele in timp real

# Bucla de simulare cu preturi infinite
try:
    while True:
        price = random.uniform(1, 100)
        full_prices.append(price)  # Adaugam pretul la graficul complet
        price_window.process_price(price)
        plot_graphs(price_window, full_prices)
        time.sleep(1.2)  # Mic delay pentru a simula timp real
except KeyboardInterrupt:
    print("Simulare oprita manual.")

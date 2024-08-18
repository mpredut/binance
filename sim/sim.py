import numpy as np
import matplotlib.pyplot as plt
import random
import random
import time
import matplotlib.pyplot as plt
from collections import deque

class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-5):
        self.window_size = window_size
        self.prices = deque()  # Păstrează toate prețurile din fereastră
        self.min_deque = deque()  # Gestionarea minimului
        self.max_deque = deque()  # Gestionarea maximului
        self.current_index = 0  # Contor intern pentru a urmări indexul
        self.max_index = max_index  # Pragul la care se face normalizarea
        self.epsilon = epsilon  # Toleranță pentru minimurile aproximativ egale

    def normalize_indices(self):
        """Normalizare indicilor când se atinge max_index."""
        min_index = self.min_deque[0][0] if self.min_deque else 0
        self.min_deque = deque([(index - min_index, price) for index, price in self.min_deque])
        self.max_deque = deque([(index - min_index, price) for index, price in self.max_deque])
        self.current_index -= min_index  # Ajustăm indexul curent

    def process_price(self, price):
        # Adăugăm noul preț la lista de prețuri
        self.prices.append(price)

        # Eliminăm prețurile care ies din fereastră
        if len(self.prices) > self.window_size:
            self.prices.popleft()

        # Gestionarea minimului și maximului curent
        self._manage_minimum(price)
        self._manage_maximum(price)

        # Incrementăm indexul intern
        self.current_index += 1

    def _manage_minimum(self, price):
        """Gestionarea minimului curent din fereastră."""
        # Normalizăm indicii dacă atingem max_index
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Eliminăm elementele care sunt în afara ferestrei (prea vechi)
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            self.min_deque.popleft()

        # Verificăm dacă prețul curent este aproximativ egal cu oricare preț existent în `min_deque`
        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon:
                return  # Nu adăugăm prețul curent dacă există deja un echivalent
        
        # Eliminăm elementele din spate mai mari decât prețul curent
        while self.min_deque and self.min_deque[-1][1] > price:
            self.min_deque.pop()

        # Adăugăm prețul curent
        self.min_deque.append((self.current_index, price))

    def _manage_maximum(self, price):
        """Gestionarea maximului curent din fereastră."""
        # Normalizăm indicii dacă atingem max_index
        if self.current_index >= self.max_index:
            self.normalize_indices()

        # Eliminăm elementele care sunt în afara ferestrei (prea vechi)
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            self.max_deque.popleft()

        # Eliminăm elementele din spate mai mici decât prețul curent (pentru a păstra ultimul maxim)
        while self.max_deque and self.max_deque[-1][1] <= price:
            self.max_deque.pop()

        # Adăugăm prețul curent
        self.max_deque.append((self.current_index, price))

    def get_min(self):
        """Returnează minimul curent din fereastră și poziția relativă."""
        if not self.min_deque:
            return None, None
        min_index, min_price = self.min_deque[0]
        relative_position = min_index - (self.current_index - len(self.prices))
        return min_price, relative_position

    def get_max(self):
        """Returnează maximul curent din fereastră și poziția relativă."""
        if not self.max_deque:
            return None, None
        max_index, max_price = self.max_deque[-1]
        relative_position = max_index - (self.current_index - len(self.prices))
        return max_price, relative_position

    def get_prices(self):
        """Returnează toate prețurile curente din fereastră."""
        return list(self.prices)

    def plot_window(self):
        """Plotează valorile din fereastră și punctează minimul și maximul."""
        prices = self.get_prices()
        min_price, min_pos = self.get_min()
        max_price, max_pos = self.get_max()
        
        plt.clf()  # Curăță figura curentă pentru a pregăti noul grafic
        plt.plot(prices, marker='o', linestyle='-', label='Prices')
        
        
        plt.title('Price Window with Min and Max')
        plt.xlabel('Relative Position in Window')
        plt.ylabel('Price')
        plt.legend()
        plt.grid(True)
        plt.pause(0.1)  # Pauză scurtă pentru a actualiza graficul

# Simularea în timp real cu valori aleatorii
window_size = 10
price_window = PriceWindow(window_size)
random.seed(42)

plt.ion()  # Activăm modul interactiv pentru a vizualiza graficele în timp real
# Bucla de simulare cu prețuri infinite
try:
    while True:
        price = random.uniform(1, 100)
        price_window.process_price(price)
        price_window.plot_window()
        time.sleep(7.5)  # Mic delay pentru a simula timp real
except KeyboardInterrupt:
    print("Simulare oprită manual.")


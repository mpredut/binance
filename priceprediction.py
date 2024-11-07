import numpy as np
from collections import deque
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from tensorflow.keras.optimizers import Adam
from sklearn.preprocessing import MinMaxScaler

class PricePrediction:
    def __init__(self, window_size):
        self.prices = []
        #self.prices = prices
        self.window_size = int(window_size)
        self.model = self.build_lstm_model()
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.trained = False  # Marcare dacă modelul a fost antrenat sau nu

    def build_lstm_model(self):
        model = Sequential()
        model.add(LSTM(50, activation='relu', input_shape=(self.window_size, 1)))
        model.add(Dense(1))
        model.compile(optimizer=Adam(learning_rate=0.001), loss='mse')
        return model

    def process_price(self, price):
        self.prices.append(price)
        if len(self.prices) < self.window_size:
            return
        # Antrenăm modelul dacă nu a fost antrenat încă
		#if not self.trained:
		#	self.train_lstm_model()
		#	self.trained = True
        
        self.train_lstm_model()
        self.trained = True
        
		# Facem predicția pe baza ferestrei curente de prețuri
		#prediction = self.predict_next_price()
		#print(f"Prețul prezis pentru următoarea perioadă: {prediction:.2f}")
    def process_prices(self, prices):
        if len(prices) < self.window_size:
            return
        self.prices = prices
        # Antrenăm modelul dacă nu a fost antrenat încă
		#if not self.trained:
		#	self.train_lstm_model()
		#	self.trained = True
        
        self.train_lstm_model()
        self.trained = True
        
    def prepare_data(self):
        prices_array = np.array(self.prices).reshape(-1, 1)
        prices_scaled = self.scaler.fit_transform(prices_array)

        X, y = [], []
        for i in range(len(prices_scaled) - self.window_size):
            X.append(prices_scaled[i:i + self.window_size])
            y.append(prices_scaled[i + self.window_size])

        X, y = np.array(X), np.array(y)

        # Check if X and y have the correct shapes
        print(f"X shape: {X.shape}, y shape: {y.shape}")

        return X, y


    def train_lstm_model(self):
        # Pregătim datele pentru antrenare
        X, y = self.prepare_data()
        if X.size == 0 or y.size == 0:
            print("Insufficient data to train the model.")
        return
        # Antrenăm modelul LSTM
        self.model.fit(X, y, epochs=100, verbose=1)
        ##print("Modelul LSTM a fost antrenat cu succes!")

    def predict_next_price(self):
        if self.trained == False:
            return None
            
        # Folosim ultima secvență de prețuri pentru predicție
        last_prices = np.array(self.prices).reshape(-1, 1)
        last_prices_scaled = self.scaler.transform(last_prices).reshape(1, self.window_size, 1)
        
        # Realizăm predicția
        predicted_price_scaled = self.model.predict(last_prices_scaled)
        
        # Rescalăm valoarea prezisă înapoi la intervalul original
        predicted_price = self.scaler.inverse_transform(predicted_price_scaled)
        return predicted_price[0][0]

# Exemplu de utilizare:
#price_window = PriceWindow(window_size=10)

# Simulăm adăugarea de prețuri secvențiale și rularea LSTM
#for price in [100, 102, 105, 108, 110, 115, 117, 120, 125, 130, 135]:
    #price_window.process_price(price)

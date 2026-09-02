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
        self.trained = False  # Track whether the model has been trained.

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
        # Train the model if it has not been trained yet.
		#if not self.trained:
		#	self.train_lstm_model()
		#	self.trained = True
        
        self.train_lstm_model()
        self.trained = True
        if len(self.prices) % 5 == 0:
            #self.train_lstm_model()
            self.trained = True


    def process_prices(self, prices):
        if len(prices) < self.window_size:
            return
        self.prices = prices
        # Train the model if it has not been trained yet.
		#if not self.trained:
		#	self.train_lstm_model()
		#	self.trained = True
        
        self.train_lstm_model()
        self.trained = True
        
    def prepare_data_old(self):
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

    def prepare_data(self):
        prices_array = np.array(self.prices).reshape(-1, 1)
        self.scaler.fit(prices_array)  # Reset the scaler across all historical data.
        prices_scaled = self.scaler.transform(prices_array)

        X, y = [], []
        for i in range(len(prices_scaled) - self.window_size):
            X.append(prices_scaled[i:i + self.window_size])
            y.append(prices_scaled[i + self.window_size])

        return np.array(X), np.array(y)

    
    
    
    def train_lstm_model(self):
        # Prepare training data.
        X, y = self.prepare_data()
        if X.size == 0 or y.size == 0:
            print("Insufficient data to train the model.")
        return
        # Train the LSTM model.
        self.model.fit(X, y, epochs=100, verbose=1)
        ##print("The LSTM model was trained successfully!")

    def predict_next_price_old(self):
        if self.trained == False:
            return None
            
        # Use the latest price sequence for prediction.
        last_prices = np.array(self.prices).reshape(-1, 1)
        last_prices_scaled = self.scaler.transform(last_prices).reshape(1, self.window_size, 1)
        
        # Make the prediction.
        predicted_price_scaled = self.model.predict(last_prices_scaled)
        
        # Scale the prediction back to the original range.
        predicted_price = self.scaler.inverse_transform(predicted_price_scaled)
        return predicted_price[0][0]



    def predict_next_price(self):
        if not self.trained:
            return None
        
        # Use all price data for prediction.
        all_prices = np.array(self.prices).reshape(-1, 1)
        all_prices_scaled = self.scaler.transform(all_prices)
        
        # Select the latest `window_size` segment from history for prediction.
        input_sequence = all_prices_scaled[-self.window_size:].reshape(1, self.window_size, 1)
        
        # Make the prediction.
        predicted_price_scaled = self.model.predict(input_sequence)
        
        # Scale the prediction back to the original range.
        predicted_price = self.scaler.inverse_transform(predicted_price_scaled)
        return predicted_price[0][0]


# Usage example:
#price_window = PriceWindow(window_size=10)

# Simulate adding sequential prices and running the LSTM.
#for price in [100, 102, 105, 108, 110, 115, 117, 120, 125, 130, 135]:
    #price_window.process_price(price)

import threading
import time
import ccxt
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
import os

class CryptoPredictor:
    def __init__(self, symbol="BTC/USDT", timeframe="1d", look_back=60, model_path="crypto_model.h5", update_interval=60, predict_interval=6):
        self.symbol = symbol
        self.timeframe = timeframe
        self.look_back = look_back
        self.model_path = model_path
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.model = None
        self.update_interval = update_interval  # De exemplu, 86400 secunde = 1 zi
        self.predict_interval = predict_interval  # De exemplu, 3600 secunde = 1 oră

    def fetch_data(self, limit=500):
        """ Colectează datele istorice de la Binance """
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def preprocess_data(self, data):
        """ Normalizează și structurează datele pentru antrenarea LSTM """
        data_close = data[['close']].values
        scaled_data = self.scaler.fit_transform(data_close)
        
        X, y = [], []
        for i in range(self.look_back, len(scaled_data)):
            X.append(scaled_data[i-self.look_back:i, 0])
            y.append(scaled_data[i, 0])
        
        X, y = np.array(X), np.array(y)
        X = np.reshape(X, (X.shape[0], X.shape[1], 1))
        return X, y

    def build_model(self):
        model = Sequential()
        model.add(LSTM(units=50, return_sequences=True, input_shape=(self.look_back, 1)))
        model.add(Dropout(0.2))
        model.add(LSTM(units=50, return_sequences=True))
        model.add(Dropout(0.2))
        model.add(LSTM(units=50))
        model.add(Dropout(0.2))
        model.add(Dense(units=1))

        model.compile(optimizer='adam', loss='mean_squared_error')
        self.model = model

    def train_model(self, epochs=50, batch_size=32):
        """ Antrenează modelul și salvează-l la final """
        data = self.fetch_data()
        X_train, y_train = self.preprocess_data(data)
        
        if self.model is None:
            self.build_model()
        
        self.model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size)
        self.model.save(self.model_path)
        print("Modelul a fost antrenat și salvat.")

    def load_model(self):
        """ Încarcă modelul salvat pentru predicții """
        if os.path.exists(self.model_path):
            self.model = load_model(self.model_path)
            print("Modelul a fost încărcat.")
        else:
            print("Modelul nu a fost găsit. Antrenează-l mai întâi.")

    def predict_next_day(self):
        print(f"Predict");
        data = self.fetch_data(limit=self.look_back)
        data_close = data[['close']].values
        print(f"Predict");
        scaled_data = self.scaler.transform(data_close)
        print(f"Predict");
        last_days = scaled_data[-self.look_back:]  # Ultimele 60 de zile pentru predicție
        last_days = np.reshape(last_days, (1, self.look_back, 1))
        print(f"Predict");
        predicted_price = self.model.predict(last_days)
        print(f"Predict");
        predicted_price = self.scaler.inverse_transform(predicted_price)
        print(f"Predicția prețului pentru ziua următoare: {predicted_price[0][0]}")
        return predicted_price[0][0]

    def update_model(self, epochs=10, batch_size=32):
        """ Actualizează modelul cu noi date și îl reantrenează parțial """
        data = self.fetch_data()
        X_train, y_train = self.preprocess_data(data)
        
        if self.model is None:
            self.load_model()
        
        self.model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size)
        self.model.save(self.model_path)
        print("Modelul a fost actualizat și salvat din nou.")

    def start_update_service(self):
        """ Rulează periodic serviciul de actualizare al modelului """
        while True:
            self.update_model(epochs=10)  # Actualizare periodică (poți ajusta numărul de epoci)
            self.start_prediction_service()
            time.sleep(self.update_interval)  # Așteaptă intervalul de actualizare (ex. 1 zi)

    def start_prediction_service(self):
        """ Rulează periodic serviciul de predicție """
        while True:
            self.predict_next_day()  # Predicție periodică
            time.sleep(self.predict_interval)  # Așteaptă intervalul de predicție (ex. 1 oră)


# Inițializare model și antrenare inițială
predictor = CryptoPredictor()

# Antrenează modelul pentru prima dată (doar dacă modelul nu există)
#predictor.train_model(epochs=50)

# Pornirea serviciilor pe thread-uri separate
update_thread = threading.Thread(target=predictor.start_update_service)
predict_thread = threading.Thread(target=predictor.start_prediction_service)


# Pornirea thread-urilor
update_thread.start()
#predict_thread.start()


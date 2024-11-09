import ccxt
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dropout, Dense
import time
import os
import threading

class CryptoPredictor:
    def __init__(self, symbol="BTC/USDT", timeframe="30m", look_back=60, model_path="crypto_model.h5", update_interval=60, predict_interval=6):
        """
        Constructorul clasei CryptoPredictor.
        :param symbol: Simbolul criptomonedei (ex. "BTC/USDT").
        :param timeframe: Intervalul de timp pentru datele colectate (ex. "20m" pentru 20 de minute).
        :param look_back: Numărul de minute istorice folosite pentru predicție.
        :param model_path: Calea fișierului unde este salvat modelul.
        :param update_interval: Intervalul de timp (în secunde) pentru actualizarea modelului.
        :param predict_interval: Intervalul de timp (în secunde) pentru a face o predicție.
        """
        self.symbol = symbol
        self.timeframe = timeframe  # Modifică aici la "20m" pentru datele de 20 minute
        self.look_back = look_back
        self.model_path = model_path
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.model = None
        self.update_interval = update_interval
        self.predict_interval = predict_interval

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
        """ Construiește modelul LSTM cu Dropout """
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

    def predict_next(self):
        """ Predicția prețului pentru următoarea perioadă de 20 de minute """
        data = self.fetch_data(limit=self.look_back)
        data_close = data[['close']].values
        scaled_data = self.scaler.transform(data_close)
        
        last_days = scaled_data[-self.look_back:]  # Ultimele 60 de minute pentru predicție
        last_days = np.reshape(last_days, (1, self.look_back, 1))
        
        predicted_price = self.model.predict(last_days)
        predicted_price = self.scaler.inverse_transform(predicted_price)  # Inversează normalizarea
        return predicted_price[0][0]

    def start_update_service(self):
        """ Actualizează modelul periodic """
        while True:
            print("Actualizare model...")
            self.train_model(epochs=1, batch_size=32)  # Antrenează periodic
            time.sleep(self.update_interval)

    def start_prediction_service(self):
        """ Serviciu de predicție periodică """
        while True:
            predicted_price = self.predict_next()
            print(f"Prețul estimat pentru următoarea perioadă: {predicted_price}")
            time.sleep(self.predict_interval)

# Crearea obiectului CryptoPredictor
predictor = CryptoPredictor()

# Încarcă modelul salvat
predictor.load_model()

# Rulează serviciul de actualizare periodică și cel de predicție în paralel
update_thread = threading.Thread(target=predictor.start_update_service)
predict_thread = threading.Thread(target=predictor.start_prediction_service)

update_thread.start()
time.sleep(60)
predict_thread.start()

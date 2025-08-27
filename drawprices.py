
import os
import math
import json
from datetime import datetime
from typing import List, Dict, Tuple, Optional

import numpy as np
import matplotlib.pyplot as plt

### my import
import symbols as sym
import cacheManager as cm


def get_cache_for_symbol(symbol: str) -> List[Tuple[int, float]]:
    raw = cm.price_cache_manager[symbol].cache
    return [(int(ts), float(p)) for ts, p in raw]


def to_series(cache_pairs: List[Tuple[int, float]]):
    """Transformă perechi [timestamp_ms, price] în numpy arrays sortate cronologic."""
    if not cache_pairs:
        return np.array([]), np.array([]), []
    # sortare cronologică
    cache_pairs = sorted(cache_pairs, key=lambda x: x[0])
    ts = np.array([int(x[0]) for x in cache_pairs], dtype=np.int64)
    prices = np.array([float(x[1]) for x in cache_pairs], dtype=np.float64)
    dt = [datetime.utcfromtimestamp(int(t)/1000.0) for t in ts]
    return ts, prices, dt

def ensure_dir(path: str):
    if path and not os.path.exists(path):
        os.makedirs(path)

def min_max_scale(arr: np.ndarray):
    """Scalează în [0,1] fără sklearn; returnează (scaled, min, max)."""
    vmin = float(np.min(arr))
    vmax = float(np.max(arr))
    if vmax == vmin:
        # serie constantă -> totul devine 0.5 ca să evităm diviziunea la zero
        return np.full_like(arr, 0.5), vmin, vmax
    scaled = (arr - vmin) / (vmax - vmin)
    return scaled, vmin, vmax

def min_max_inverse(arr_scaled: np.ndarray, vmin: float, vmax: float):
    if vmax == vmin:
        return np.full_like(arr_scaled, vmin)
    return arr_scaled * (vmax - vmin) + vmin


# ==========================
# 3) PLOT ISTORIC
# ==========================

def plot_history(symbol: str, dt, prices: np.ndarray, outdir="plots"):
    ensure_dir(outdir)
    plt.figure(figsize=(10, 5))
    plt.plot(dt, prices, marker="o", linestyle="-")
    plt.title(f"Evoluția prețului - {symbol}")
    plt.xlabel("Timp")
    plt.ylabel("Preț")
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()
    fname = os.path.join(outdir, f"history_{symbol}.png")
    plt.savefig(fname, dpi=150)
    plt.show()   # 👈 afiseaza pe ecran
    plt.close()
    return fname


# ==========================
# 4) REGRESIE LINIARĂ (np.polyfit)
# ==========================

def forecast_linear(ts_ms: np.ndarray, prices: np.ndarray, horizon: int = 20):
    """
    Regresie pe index-ul punctelor (nu pe timp în ms, ca să fie stabil numeric).
    Dacă vrei pe timp real, poți normaliza ms la zile/ore și folosi aceleași formule.
    """
    n = len(prices)
    x = np.arange(n, dtype=np.float64)
    # fit y ~ a*x + b
    a, b = np.polyfit(x, prices, 1)
    # predicții pentru pașii următori
    x_future = np.arange(n, n + horizon, dtype=np.float64)
    y_future = a * x_future + b
    return y_future

def plot_with_linear(symbol: str, dt, prices: np.ndarray, lin_forecast: np.ndarray, outdir="plots"):
    ensure_dir(outdir)
    n = len(prices)
    future_dt = []
    if n >= 2:
        avg_step = (dt[-1] - dt[0]).total_seconds() / max(1, (n-1))
        future_dt = [dt[-1] + (i+1)* (dt[-1]-dt[-2] if n>1 else timedelta(seconds=avg_step))
                     for i in range(len(lin_forecast))]
    plt.figure(figsize=(10, 5))
    plt.plot(dt, prices, marker="o", linestyle="-", label="Istoric")
    if future_dt:
        plt.plot(future_dt, lin_forecast, marker="o", linestyle="--", label="Regresie (forecast)")
    plt.title(f"Forecast (Regresie liniară) - {symbol}")
    plt.xlabel("Timp")
    plt.ylabel("Preț")
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    fname = os.path.join(outdir, f"forecast_linear_{symbol}.png")
    plt.savefig(fname, dpi=150)
    plt.show()   # 👈 afiseaza pe ecran
    plt.close()
    return fname


# ==========================
# 5) LSTM
# ==========================

def make_sequences(series: np.ndarray, window: int):
    X, y = [], []
    for i in range(window, len(series)):
        X.append(series[i-window:i])
        y.append(series[i])
    X = np.array(X)
    y = np.array(y)
    # reshape la (samples, timesteps, features)
    X = X.reshape((X.shape[0], X.shape[1], 1))
    return X, y

def try_import_tf():
    try:
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense
        return True, tf, Sequential, LSTM, Dense
    except Exception as e:
        return False, e, None, None, None

def forecast_lstm(prices: np.ndarray, window: int = 20, epochs: int = 50, batch_size: int = 16,
                  horizon: int = 20, verbose: int = 0):
    """
    Întoarce (predicții, info_text). Dacă TensorFlow lipsește sau seria e prea scurtă,
    întoarce (None, motiv).
    """
    ok, tf_or_err, Sequential, LSTM, Dense = try_import_tf()
    if not ok:
        return None, f"LSTM indisponibil: {tf_or_err}"

    if len(prices) < window + 5:
        return None, f"Seria e prea scurtă pentru LSTM (minim ~{window+5} puncte)."

    # scalare [0,1]
    scaled, vmin, vmax = min_max_scale(prices)
    X, y = make_sequences(scaled, window)
    # împărțire train/val simplă
    split = max(window, int(0.8 * len(X)))
    X_train, y_train = X[:split], y[:split]
    X_val, y_val = X[split:], y[split:]

    model = Sequential()
    model.add(LSTM(64, input_shape=(window, 1)))
    model.add(Dense(1))
    model.compile(optimizer="adam", loss="mse")
    model.fit(X_train, y_train,
              validation_data=(X_val, y_val) if len(X_val) else None,
              epochs=epochs, batch_size=batch_size, verbose=verbose)

    # forecast iterativ pe orizontul cerut
    last_window = scaled[-window:].reshape(1, window, 1)
    preds_scaled = []
    for _ in range(horizon):
        pred = model.predict(last_window, verbose=0)[0, 0]
        preds_scaled.append(pred)
        # slide window
        last_window = np.concatenate([last_window[:, 1:, :], pred.reshape(1, 1, 1)], axis=1)

    preds = min_max_inverse(np.array(preds_scaled), vmin, vmax)
    return preds, f"LSTM antrenat: window={window}, epochs={epochs}, batch={batch_size}"
    
def plot_with_lstm(symbol: str, dt, prices: np.ndarray, lstm_forecast: Optional[np.ndarray], outdir="plots"):
    ensure_dir(outdir)
    n = len(prices)
    future_dt = []

    if lstm_forecast is not None and len(lstm_forecast) > 0 and n >= 2:
        step = (dt[-1] - dt[-2])
        if step.total_seconds() <= 0:
            from datetime import timedelta
            avg_step = (dt[-1] - dt[0]).total_seconds() / max(1, (n-1))
            step = timedelta(seconds=avg_step)

        future_dt = [dt[-1] + step*(i+1) for i in range(len(lstm_forecast))]

    plt.figure(figsize=(10, 5))
    plt.plot(dt, prices, marker="o", linestyle="-", label="Istoric")
    if lstm_forecast is not None and len(lstm_forecast) > 0 and future_dt:
        plt.plot(future_dt, lstm_forecast, marker="o", linestyle="--", label="LSTM (forecast)")
    plt.title(f"Forecast (LSTM) - {symbol}")
    plt.xlabel("Timp")
    plt.ylabel("Preț")
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    fname = os.path.join(outdir, f"forecast_lstm_{symbol}.png")
    plt.savefig(fname, dpi=150)
    plt.show()   # 👈 afiseaza pe ecran
    plt.close()
    return fname


# ==========================
# 6) PIPELINE PE TOATE SYMBOLOURILE
# ==========================

def run_for_symbols(symbols: List[str],
                    outdir: str = "plots",
                    horizon: int = 20,
                    lstm_window: int = 20,
                    lstm_epochs: int = 50,
                    lstm_batch: int = 16,
                    lstm_verbose: int = 0):
    ensure_dir(outdir)
    results = {}
    print(f" MARIUS OK: ")
    for symbol in symbols:
        print(f" MARIUS SYM:{symbol} ")
        pairs = get_cache_for_symbol(symbol)
        ts, prices, dt = to_series(pairs)

        if len(prices) == 0:
            results[symbol] = {"error": "Nu există date în cache."}
            continue

        print(f" plot history{symbol} ")
        
        hist_path = plot_history(symbol, dt, prices, outdir=outdir)

        print(f" regresie:{symbol} ")
        # Regresie
        lin_pred = forecast_linear(ts, prices, horizon=horizon)
        lin_path = plot_with_linear(symbol, dt, prices, lin_pred, outdir=outdir)

        # LSTM (dacă posibil)
        print(f" forecast_lstm :{symbol} ")
        lstm_pred, info = forecast_lstm(prices,
                                        window=lstm_window,
                                        epochs=lstm_epochs,
                                        batch_size=lstm_batch,
                                        horizon=horizon,
                                        verbose=lstm_verbose)
        lstm_path = None
        if lstm_pred is not None:
            lstm_path = plot_with_lstm(symbol, dt, prices, lstm_pred, outdir=outdir)

        results[symbol] = {
            "history_png": hist_path,
            "linear_forecast_png": lin_path,
            "lstm_info": info,
            "lstm_forecast_png": lstm_path,
        }
        print(f"[{symbol}] OK: {results[symbol]}")
    return results


# ==========================
# 7) MAIN
# ==========================

if __name__ == "__main__":
   
    symbols = sym.symbols

    # 2) rulezi pipeline-ul
    res = run_for_symbols(
        symbols,
        outdir="plots",
        horizon=20,       # câte puncte viitoare să estimezi
        lstm_window=10,   # fereastra pentru LSTM (alege 20-60 dacă ai serii mai lungi)
        lstm_epochs=50,   # crește pentru acuratețe (și timp mai mare)
        lstm_batch=16,
        lstm_verbose=0
    )

    # 3) (opțional) salvezi un raport JSON cu rezultatele
    with open("forecast_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\nImaginile au fost salvate în folderul ./plots și rezumatul în forecast_results.json")

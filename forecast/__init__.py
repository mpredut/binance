"""Price forecasting and long-term trend analysis.

forecast        — walk-forward benchmark (lindy/logit/boost) and forecast.json writer.
priceprediction — dormant Keras LSTM requiring TensorFlow.
trend_stats     — Mann-Kendall slope significance and Hurst regime.
trend_survival  — empirical per-coin trend duration plus estimate_T/hybrid_T/fetch_klines.

From outside the package:  from forecast.trend_stats import mann_kendall
                           from forecast.trend_survival import estimate_T
"""

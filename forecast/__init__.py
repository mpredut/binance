"""forecast — predicție preț / analiză trend long-term.

forecast        — benchmark walk-forward (lindy/logit/boost) + scrie forecast.json.
priceprediction — LSTM Keras (dormant; necesită tensorflow).
trend_stats     — Mann-Kendall (semnificația pantei) + Hurst (regimul).
trend_survival  — durata empirică a trendului per monedă (estimate_T, hybrid_T) + fetch_klines.

Din afara pachetului:  from forecast.trend_stats import mann_kendall
                       from forecast.trend_survival import estimate_T
"""

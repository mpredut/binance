"""trend — analiză de trend / predicție preț long-term (primitive reutilizabile).

trend_stats    — Mann-Kendall (semnificația pantei) + Hurst (regimul).
trend_survival — durata empirică a trendului per monedă (estimate_T, hybrid_T,
                 curba de supraviețuire) + fetch_klines.

Folosibile ca:  from trend.trend_stats import mann_kendall
                from trend.trend_survival import estimate_T
"""

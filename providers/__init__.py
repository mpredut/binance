"""providers — stratul de fațadă pentru piețe (market data + ordine).

market_api          — fațada unificată (MarketApi + MarketDataProvider ABC + BinanceProvider
                      inline + singletonul `api`); rutează pe symbol către providerul potrivit.
kraken_provider     — adaptor Kraken (explicit-only) peste kraken/kraken_client.
hyperliquid_provider— adaptor Hyperliquid (revendică HYPE) peste SDK-ul HL.
t212_provider       — adaptor Trading212 (explicit-only) peste 212trading/t212_client.

Din afară:  from providers.market_api import api
NB: __init__ NU importă market_api eager (ar fi circular cu bucla de înregistrare).
"""

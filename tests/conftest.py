# Exclude din colectare scripturile care NU sunt teste unitare, ci validări care
# lovesc API-ul REAL Binance la import (chei/clock/rețea) → altfel pytest abandonează
# colectarea întregii suite. Rulează-le manual dacă ai nevoie.
collect_ignore = ["key_test.py", "key2_test.py"]

"""
cachepaths.py — locul unic care decide UNDE stau fișierele de cache.

Toate fișierele cache (cache_*.json/.jsonl + .meta) trăiesc în subfolderul
`cachedb/`. `cache_path(name)` prefixează un nume simplu cu acel folder (creat
la nevoie). Numele care au DEJA o cale (absolută sau cu separator — ex. teste,
migrare) sunt lăsate neatinse.

Se poate suprascrie folderul prin variabila de mediu BINANCE_CACHE_DIR.
"""
import os

CACHE_DIR = os.environ.get(
    "BINANCE_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cachedb"),
)


def cache_path(name):
    """Întoarce calea fișierului de cache în `cachedb/`.
    Dacă `name` are deja o cale (absolut sau conține un separator de directoare),
    e returnat ca atare → nu stricăm căi explicite (teste, migrare, etc.)."""
    if not name:
        return name
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)

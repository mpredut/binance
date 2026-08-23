"""
cachepaths.py — the single authority that determines where cache files live.

All cache files (cache_*.json/.jsonl plus .meta) live in the ``cachedb/``
subdirectory. ``cache_path(name)`` prefixes a simple name with that directory,
creating it when needed. Names that already contain an absolute or separator-
qualified path, such as test or migration paths, remain unchanged.

The BINANCE_CACHE_DIR environment variable can override the directory.
"""
import os

CACHE_DIR = os.environ.get(
    "BINANCE_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cachedb"),
)


def cache_path(name):
    """Return the cache-file path under ``cachedb/``.

    If ``name`` is already absolute or contains a directory separator, return it
    unchanged so explicit test, migration, and other paths remain intact.
    """
    if not name:
        return name
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)

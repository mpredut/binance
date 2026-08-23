#!/usr/bin/env python3
"""
alerts_config.py — load the plain-text market_alerts.conf file for the alert monitor.

Line-oriented format (# starts a full-line or inline comment):
    watch    = BTC, TAO, HYPE          # watchlist (coins that are always monitored)
    sources  = coinmarketcap, coingecko
    default  = 4.1 / 7.5               # default threshold: UP% / DOWN%
    new_coin = 12 / 25                 # threshold for new coins
    BTC      = 6 / 10                  # PER-COIN threshold (any symbol)
    cooldown_minutes = 30              # scalar settings (see _SETTING_KEYS)

If the file or a key is absent, the defaults below are used. A short configuration
is therefore valid and needs to contain only the desired overrides.
"""
from __future__ import annotations

import copy
import os

_DEFAULTS = {
    "watch": ["BTC", "TAO", "HYPE"],
    "sources": ["coinmarketcap", "coingecko"],
    "discover_new_coins": True,   # no/false => watchlist only; no new-coin alerts
    "max_monitored": 20,
    "max_new_coins": 15,
    "new_coins_scan_seconds": 3600,
    "price_scan_seconds": 60,
    # Exact PriceChecker shape: default/dynamic/cooldown/lookback plus per_coin.
    "alert_config": {
        "default":  {"up_percent": 4.1, "down_percent": 7.5},
        "dynamic":  {"up_percent": 12.0, "down_percent": 25.0},
        "per_coin": {},
        "cooldown_minutes": 30,
        "lookback_hours": 24,
    },
}

# Scalar key -> (type, destination: "ac" in alert_config or "top" in cfg).
_SETTING_KEYS = {
    "cooldown_minutes": (int, "ac"), "lookback_hours": (int, "ac"),
    "max_monitored": (int, "top"), "max_new_coins": (int, "top"),
    "new_coins_scan_seconds": (int, "top"), "price_scan_seconds": (int, "top"),
}
_LIST_KEYS = {"watch": str.upper, "sources": str.lower}
_BUCKET_ALIAS = {"default": "default", "new_coin": "dynamic"}  # conf new_coin -> internal dynamic


def _pair(val: str) -> dict:
    """'6 / 10' -> {'up_percent': 6.0, 'down_percent': 10.0}."""
    up, _, down = val.partition("/")
    return {"up_percent": float(up.strip()), "down_percent": float(down.strip())}


def load_config(path: str) -> dict:
    cfg = copy.deepcopy(_DEFAULTS)
    if not path or not os.path.exists(path):
        return cfg
    ac = cfg["alert_config"]
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            try:
                if key == "discover_new_coins":
                    cfg[key] = val.strip().lower() in ("yes", "true", "1", "on", "da")
                elif key in _LIST_KEYS:
                    norm = _LIST_KEYS[key]
                    cfg[key] = [norm(x.strip()) for x in val.split(",") if x.strip()]
                elif key in _SETTING_KEYS:
                    typ, where = _SETTING_KEYS[key]
                    v = typ(float(val))
                    (ac if where == "ac" else cfg)[key] = v
                elif "/" in val:                          # an UP/DOWN threshold
                    pair = _pair(val)
                    if key in _BUCKET_ALIAS:
                        ac[_BUCKET_ALIAS[key]] = pair
                    else:                                 # every other name is a per-coin key
                        ac["per_coin"][key.upper()] = pair
            except ValueError:
                pass  # Ignore malformed lines and retain the default.
    return cfg


def resolve(alert_config: dict, symbol: str, is_dynamic: bool) -> dict:
    """Resolve a coin threshold: per_coin, then dynamic for new coins, then default."""
    per = alert_config.get("per_coin", {})
    if symbol in per:
        return per[symbol]
    return alert_config["dynamic"] if is_dynamic else alert_config["default"]


if __name__ == "__main__":
    import json
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "market_alerts.conf"
    print(json.dumps(load_config(p), indent=2))

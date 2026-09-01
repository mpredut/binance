#!/usr/bin/env python3
"""Forward shadow for HYPE spot on Hyperliquid without order access.

Use the faithful ``strategies.spot_dca`` engine through shared replay and public
Hyperliquid candles. Never read or write live state, orders, or balances. Variants
are preregistered and remain PAPER:

* ``current``: effective HLC configuration;
* ``long_tp3_trail3``: TP armed at 3%, trend hold, fixed 3% trailing;
* ``reentry4``: current configuration with reentry after a 4% pullback;
* ``trail_profit_floor_sl18``: trailing only above +1%, hard stop at -18%;
* ``overlay650t8``: trend overlay with 650 top-up and 8% trailing.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KRAKEN_DIR = os.path.join(ROOT, "kraken")
for path in (ROOT, HERE, KRAKEN_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import shadow_live as shadow  # noqa: E402


LOG_DIR = os.path.join(ROOT, "logs", "hyperliquid_shadow")


def _load_config() -> None:
    from common import load_dotenv

    load_dotenv(os.path.join(HERE, ".env"))
    load_dotenv(os.path.join(HERE, "config.env"))


def _variants(interval: int):
    if interval != 240:
        raise ValueError("shadow_longterm accepts only the native 240m interval")
    _load_config()
    from strategies.spot_dca import StratParams

    base = StratParams.from_env()
    variants = {
        "current": base,
        "long_tp3_trail3": dataclasses.replace(
            base,
            takeprofit_pct=3.0,
            tp_trend_hold=True,
            tp_trail_adaptive=False,
            tp_trail_pct=3.0,
        ),
        "reentry4": dataclasses.replace(base, reentry_drop_pct=4.0),
        "trail_profit_floor_sl18": dataclasses.replace(
            base,
            tp_trail_profit_floor_pct=1.0,
            stop_loss_pct=18.0,
        ),
        "overlay650t8": dataclasses.replace(
            base,
            trend_overlay=True,
            trend_topup=650.0,
            trend_trail_pct=8.0,
            trend_exit_break=False,
        ),
    }
    return variants


def _fetch_with_ts(_pair: str, interval: int):
    if interval != 240:
        raise ValueError("candles HLC long-term sunt validate numai la 240m")
    from hl_client import HLClient

    token = os.environ.get("HL_SPOT_TOKEN") or "HYPE"
    client = HLClient()  # no secret_key keeps ``exchange`` None, allowing zero orders
    candles = client.candles(token, "4h", lookback_hours=5000 * 4)
    rows = sorted(
        (
            int(item["t"]) // 1000,
            float(item["o"]), float(item["h"]),
            float(item["l"]), float(item["c"]),
        )
        for item in candles
    )
    # The final candle may still be forming; remove it as the Kraken runner does.
    return rows[:-1]


def snapshot(*, quiet: bool = False):
    shadow.LOG_DIR = LOG_DIR
    shadow._variants = _variants
    shadow._fetch_with_ts = _fetch_with_ts
    return shadow.snapshot("HYPE-HL", 240, 0.07, quiet=quiet)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    _load_config()
    try:
        snapshot(quiet=args.quiet)
    except Exception as exc:  # visible cron failure without live effects
        print(f"[hl_shadow] eroare: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

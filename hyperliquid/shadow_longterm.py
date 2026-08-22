#!/usr/bin/env python3
"""Forward shadow HYPE spot pe Hyperliquid, fără acces la ordine.

Folosește motorul faithful ``strategies.spot_dca`` prin replay-ul comun și
candles publice Hyperliquid. Starea, ordinele și soldul live nu sunt citite sau
scrise. Variantele sunt preînregistrate și rămân PAPER:

* ``current``: configurația HLC efectivă;
* ``long_tp3_trail3``: TP armat la 3%, trend-hold, trailing fix 3%;
* ``reentry4``: configurația curentă, reintrare după recul de 4%.
* ``trail_profit_floor_sl18``: trailing numai peste +1%, hard stop la -18%;
* ``overlay650t8``: overlay de trend cu top-up 650 și trailing 8%.
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
    if interval != 240:
        raise ValueError("shadow_longterm acceptă numai intervalul nativ 240m")
    return variants


def _fetch_with_ts(_pair: str, interval: int):
    if interval != 240:
        raise ValueError("candles HLC long-term sunt validate numai la 240m")
    from hl_client import HLClient

    token = os.environ.get("HL_SPOT_TOKEN") or "HYPE"
    client = HLClient()  # fără secret_key => ``exchange`` rămâne None, zero ordine
    candles = client.candles(token, "4h", lookback_hours=5000 * 4)
    rows = sorted(
        (
            int(item["t"]) // 1000,
            float(item["o"]), float(item["h"]),
            float(item["l"]), float(item["c"]),
        )
        for item in candles
    )
    # Ultima lumânare poate fi încă în formare; o eliminăm ca în runnerul Kraken.
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
    except Exception as exc:  # cron: eșec vizibil, fără efect live
        print(f"[hl_shadow] eroare: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

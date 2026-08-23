#!/usr/bin/env python3
"""hl_dca_bot.py — base v2 spot_dca engine on Hyperliquid SPOT via HyperliquidProvider.

The SAME engine as kraken_bot (DCA + take profit + trailing + stop), with only the
venue changed. It trades only HYPE spot on HL. In provider-agnostic path B, the strategy
requires StrategyExecutor and HyperliquidProvider implements it.

  python3 hl_dca_bot.py --paper   # PAPER validation without money
  python3 hl_dca_bot.py           # REAL requires STRAT_EXECUTE=true + HL_LIVE_ORDERS=true;
                                  # provider safety-gates real orders

CAUTION: base v2 SELLS into strength at +TP%. It uses the SAME HYPE spot balance as a
directional long, so do not run base v2 and long-hold over the same HYPE.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import load_dotenv, log, single_instance   # hyperliquid/common.py
from strategies.spot_dca import Strategy, StratParams
from providers.hyperliquid_provider import HyperliquidProvider


def state_dir_for(dry_run: bool) -> str:
    """Isolate HL state from Kraken and separate PAPER from LIVE."""
    state_dir = os.path.join(_HERE, ".paper_state") if dry_run else _HERE
    if dry_run:
        os.makedirs(state_dir, exist_ok=True)
    return state_dir


def main() -> int:
    # Load configuration before CLI defaults and PAPER/REAL calculation. `.env` wins
    # because load_dotenv does not override already defined variables.
    load_dotenv(os.path.join(_HERE, ".env"))
    load_dotenv(os.path.join(_HERE, "config.env"))

    ap = argparse.ArgumentParser(description="base v2 (spot_dca) pe Hyperliquid HYPE.")
    ap.add_argument("--paper", action="store_true", help="Forteaza PAPER (fara bani)")
    ap.add_argument("--token", default=os.environ.get("HL_SPOT_TOKEN") or "HYPE")
    args = ap.parse_args()

    token = (args.token or "HYPE").upper()
    strategy_enabled = os.environ.get("STRAT_EXECUTE", "false").lower() == "true"
    venue_enabled = os.environ.get("HL_LIVE_ORDERS", "false").lower() == "true"
    strat_dry = args.paper or not (strategy_enabled and venue_enabled)
    if not any(a in sys.argv for a in ()):  # reserved for future one-shot commands
        single_instance(f"hl_dca_bot_{token}")   # one instance per token, independent of dn/hl_bot

    from providers.execution_audit import AuditedStrategyExecutor

    provider = AuditedStrategyExecutor(
        HyperliquidProvider(token=token), venue="Hyperliquid",
    )
    log("=== HL base v2 bot (spot_dca) ===")
    log(f"    token      : {token} (HYPE spot pe Hyperliquid)")
    log(f"    executie   : {'PAPER (fara bani)' if strat_dry else '⚠ REAL — BANI ADEVARATI'}")
    log("    motor      : strategies.spot_dca (IDENTIC cu kraken_bot)")
    Strategy(
        provider, token, StratParams.from_env(), dry_run=strat_dry,
        state_dir=state_dir_for(strat_dry),
        notification_source="hyperliquid", venue_label="Hyperliquid",
        fee_note="fee HL spot base ~0.04% maker / ~0.07% taker per fill",
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

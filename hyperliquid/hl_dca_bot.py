#!/usr/bin/env python3
"""hl_dca_bot.py — base v2 (motorul spot_dca) pe Hyperliquid SPOT, prin HyperliquidProvider.

ACELASI motor ca kraken_bot (strategies.spot_dca: DCA + take-profit + trailing + stop),
doar venue-ul difera. Tranzactioneaza DOAR HYPE spot pe HL. Provider-agnostic (Calea B):
strategia cere contractul StrategyExecutor; HyperliquidProvider il implementeaza.

  python3 hl_dca_bot.py --paper   # PAPER (fara bani) — VALIDARE
  python3 hl_dca_bot.py           # REAL (necesita STRAT_EXECUTE=true + HL_LIVE_ORDERS=true;
                                  #        ordinele reale sunt gated in provider pt siguranta)

ATENTIE: base v2 VINDE in putere (la +TP%). Pe HYPE spot foloseste ACELASI sold ca un
eventual long direcțional -> nu tine si base v2 si long-hold pe acelasi HYPE.
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


def main() -> int:
    # Configurația trebuie încărcată înainte de valorile implicite CLI și înainte
    # de calculul modului PAPER/REAL. `.env` câștigă deoarece load_dotenv nu
    # suprascrie variabile deja definite.
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
    if not any(a in sys.argv for a in ()):  # (rezervat pt viitoare comenzi one-shot)
        single_instance(f"hl_dca_bot_{token}")   # o instanta per token (nu se bate cu dn/hl_bot)

    provider = HyperliquidProvider(token=token)
    log("=== HL base v2 bot (spot_dca) ===")
    log(f"    token      : {token} (HYPE spot pe Hyperliquid)")
    log(f"    executie   : {'PAPER (fara bani)' if strat_dry else '⚠ REAL — BANI ADEVARATI'}")
    log("    motor      : strategies.spot_dca (IDENTIC cu kraken_bot)")
    Strategy(provider, token, StratParams.from_env(), dry_run=strat_dry).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

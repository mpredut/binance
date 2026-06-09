#!/usr/bin/env python3
"""
dn_bot.py — bot DELTA-NEUTRAL (funding farming) pe Hyperliquid.

Ruleaza cu python-ul din venv:
    /home/mariusp/binance/.venv/bin/python dn_bot.py        # dupa .env
    ...python dn_bot.py --paper                               # simulare
    ...python dn_bot.py --funding                             # arata funding-ul curent
    ...python dn_bot.py --status                              # picioarele + delta curenta

Necesita USDC in AMBELE conturi: SPOT (ca sa cumperi tokenul) si PERP (margine short).
"""

from __future__ import annotations

import argparse
import os
import sys

import time

from common import load_dotenv, log
from hl_client import HLClient, HLError
from delta_neutral import DeltaNeutral, DNParams


def _cmd_status(client: HLClient, params: DNParams) -> int:
    coin = params.coin
    spot_qty = client.spot_balance(params.spot_token)
    usdc     = client.spot_balance("USDC")
    spot_px  = client.spot_mid(params.spot_pair) or 0.0
    perp_px  = client.mid(coin) or 0.0
    fhr      = client.funding_rate(coin) or 0.0
    pos      = client.position_full(coin) or {}
    ms       = client.margin_summary()
    szi      = float(pos.get("szi") or 0)
    entry    = float(pos.get("entryPx") or 0)
    liq      = float(pos.get("liquidationPx") or 0)
    upnl     = float(pos.get("unrealizedPnl") or 0)
    delta    = spot_qty + szi
    perp_notional = abs(szi) * perp_px
    # funding real incasat (ultimele 7 zile)
    earned = 0.0
    for ev in client.funding_history(int((time.time() - 7*86400) * 1000)):
        try: earned += float(ev.get("delta", {}).get("usdc") or 0)
        except (TypeError, ValueError): pass
    est_day = fhr * perp_notional * 24

    log("=== STATUS DELTA-NEUTRAL ===")
    log(f"  SPOT (long) : {spot_qty:.4f} {params.spot_token}  (~${spot_qty*spot_px:,.2f})  px={spot_px:.4f}")
    log(f"  PERP (short): {szi:.4f} {coin}  entry={entry:.4f}  uPnL={upnl:+.2f}  (~${perp_notional:,.2f})  px={perp_px:.4f}")
    log(f"  DELTA NET   : {delta:+.4f} {coin}  ({'HEDGE OK' if abs(delta)*perp_px < 5 else 'DEZECHILIBRAT — rebalanseaza!'})")
    if liq > 0 and szi < 0:
        dist = (liq - perp_px)/perp_px*100
        flag = "⚠ PERICOL" if dist < params.liq_alert_pct else "ok"
        log(f"  LICHIDARE   : short la {liq:.4f}  (pretul mai poate urca {dist:.1f}% pana acolo)  [{flag}]")
    else:
        log(f"  LICHIDARE   : (fara short deschis)")
    log(f"  FUNDING     : {fhr*100:+.5f}%/ora  (~{fhr*24*365*100:.1f}%/an)  est. ~${est_day:+.3f}/zi pe pozitia curenta")
    log(f"  FUNDING real: ${earned:+.4f} incasat (ultimele 7 zile)")
    log(f"  COLATERAL   : USDC ${usdc:,.2f} (unified: spot+perp impart colateralul)  perp_acct=${ms.get('accountValue',0):,.2f}  margine_folosita=${ms.get('totalMarginUsed',0):,.2f}")
    return 0


def _client(need_wallet: bool) -> HLClient:
    mainnet = os.environ.get("HL_MAINNET", "true").strip().lower() != "false"
    secret = os.environ.get("HL_SECRET_KEY") if need_wallet else None
    return HLClient(secret_key=secret, account_address=os.environ.get("HL_ACCOUNT_ADDRESS"), mainnet=mainnet)


def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    ap = argparse.ArgumentParser(description="Bot delta-neutral (funding) pe Hyperliquid.")
    ap.add_argument("--env-file", default=env_file)
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--funding", action="store_true", help="Arata funding-ul curent si iese")
    ap.add_argument("--status", action="store_true", help="Arata picioarele + delta si iese")
    args = ap.parse_args()

    dry = args.paper or not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
    need_wallet = not dry
    try:
        client = _client(need_wallet)
    except HLError as e:
        log(f"! {e}"); return 1
    params = DNParams.from_env(client)

    if args.funding:
        f = client.funding_rate(params.coin)
        log(f"[FUNDING] {params.coin}: {f*100:+.4f}%/ora (~{f*24*365*100:.1f}%/an)" if f is not None else "  indisponibil")
        return 0
    if args.status:
        return _cmd_status(client, params)

    log("=== Hyperliquid DELTA-NEUTRAL bot ===")
    log(f"    coin={params.coin}  spot={params.spot_pair}  notional={params.notional} USDC/picior")
    log(f"    executie: {'PAPER (fara bani)' if dry else '⚠ REAL — BANI ADEVARATI'}")
    try:
        DeltaNeutral(client, params, dry_run=dry, desktop=False).run()
        return 0
    except KeyboardInterrupt:
        log("Oprit manual."); return 130


if __name__ == "__main__":
    raise SystemExit(main())

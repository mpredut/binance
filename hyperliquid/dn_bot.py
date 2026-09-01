#!/usr/bin/env python3
"""
dn_bot.py — DELTA-NEUTRAL funding-farming bot on Hyperliquid.

Run with the virtual-environment Python:
    /home/mariusp/binance/.venv/bin/python dn_bot.py        # use .env
    ...python dn_bot.py --paper                               # simulation
    ...python dn_bot.py --funding                             # show current funding
    ...python dn_bot.py --status                              # show legs and current delta

Requires USDC in BOTH SPOT for buying the token and PERP for short margin.
"""

from __future__ import annotations

import argparse
import os
import sys

import time

from common import load_dotenv, log, single_instance, required_bool_env
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
    # Actual funding received over the last seven days.
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


def _cmd_watch(client: HLClient, params: DNParams, desktop: bool, once: bool = False) -> int:
    """Read-only monitor of the REAL position: ZERO orders, only reads and alerts.
    Safe to run alongside the server bot for redundant supervision."""
    from notify import notify
    log("=== MONITOR DN (read-only — nu plaseaza NICIUN ordin) ===")
    log(f"    coin={params.coin}  verifica la {params.check_minutes} min  alerte: lichidare<{params.liq_alert_pct}%, delta, funding negativ, pozitie disparuta")
    armed = {"liq": True, "delta": True, "fund": True, "gone": True}
    errors = 0
    while True:
        try:
            spot_qty = client.spot_balance_strict(params.spot_token)
            pos = client.position_full(params.coin) or {}
            szi = float(pos.get("szi") or 0)
            perp_px = client.mid(params.coin) or 0.0
            fhr = client.funding_rate(params.coin)
            liq = float(pos.get("liquidationPx") or 0)
            if errors:
                log(f"  [WATCH] ✓ conexiune RECUPERATA dupa {errors} incercari esuate")
            errors = 0
            delta_usd = abs(spot_qty + szi) * perp_px
            has_pos = abs(spot_qty) * perp_px > 5 or abs(szi) * perp_px > 5

            # 1. Did the position disappear through liquidation or closure?
            if not has_pos:
                if armed["gone"]:
                    armed["gone"] = False
                    notify(title=f"👁 MONITOR {params.coin}: pozitia DN a disparut",
                           body="Nu mai vad niciun picior pe cont. Verifica botul de pe server!",
                           source="dn-watch", desktop=desktop)
            else:
                armed["gone"] = True
                # 2. Is there a large delta imbalance?
                if delta_usd > max(5.0, params.notional * params.rebalance_pct / 100):
                    if armed["delta"]:
                        armed["delta"] = False
                        notify(title=f"👁 MONITOR {params.coin}: delta ${delta_usd:.2f} — dezechilibrat",
                               body=f"spot {spot_qty:.2f} / perp {szi:.2f} — serverul ar trebui sa rebalanseze",
                               source="dn-watch", desktop=desktop)
                else:
                    armed["delta"] = True
                # 3. Is the short close to liquidation?
                if liq > 0 and szi < 0 and perp_px > 0:
                    dist = (liq - perp_px) / perp_px * 100
                    if 0 < dist <= params.liq_alert_pct and armed["liq"]:
                        armed["liq"] = False
                        notify(title=f"👁 MONITOR {params.coin}: short la {dist:.1f}% de LICHIDARE",
                               body=f"p{perp_px:.2f} liq{liq:.2f} — daca serverul nu reduce singur, intervino!",
                               source="dn-watch", desktop=desktop)
                    elif dist > params.liq_alert_pct * 1.5:
                        armed["liq"] = True
                # 4. Is funding strongly negative?
                if fhr is not None:
                    if fhr < params.exit_funding_hr and armed["fund"]:
                        armed["fund"] = False
                        notify(title=f"👁 MONITOR {params.coin}: funding negativ {fhr*100:+.4f}%/h",
                               body="Platesti funding in loc sa incasezi. Botul de pe server decide iesirea (mediere+min-hold).",
                               source="dn-watch", desktop=desktop)
                    elif fhr >= 0:
                        armed["fund"] = True
            log(f"  [WATCH] spot={spot_qty:.4f} perp={szi:.4f} delta=${delta_usd:.2f} "
                f"liq={'%.2f' % liq if liq else '-'} funding={fhr*100:+.4f}%/h px={perp_px:.4f}"
                if fhr is not None else f"  [WATCH] spot={spot_qty:.4f} perp={szi:.4f} (funding indisponibil)")
        except KeyboardInterrupt:
            log("  [WATCH] oprit manual."); return 0
        except Exception as e:  # noqa: BLE001 — one error must not terminate the monitor
            errors += 1
            log(f"  ! [WATCH] eroare (#{errors}): {e!r} — continui")
        if once:
            return 0
        time.sleep(min(params.check_minutes * 60 * (2 ** min(errors, 3)), 300))


def _client(need_wallet: bool) -> HLClient:
    mainnet = required_bool_env("HL_MAINNET")
    secret = os.environ.get("HL_SECRET_KEY") if need_wallet else None
    return HLClient(secret_key=secret, account_address=os.environ.get("HL_ACCOUNT_ADDRESS"), mainnet=mainnet)


def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)                                                      # gitignored secrets
    load_dotenv(os.path.join(os.path.dirname(env_file) or ".", "config.env"))  # versioned configuration

    ap = argparse.ArgumentParser(description="Bot delta-neutral (funding) pe Hyperliquid.")
    ap.add_argument("--env-file", default=env_file)
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--funding", action="store_true", help="Arata funding-ul curent si iese")
    ap.add_argument("--status", action="store_true", help="Arata picioarele + delta si iese")
    ap.add_argument("--watch", action="store_true",
                    help="MONITOR read-only al pozitiei reale: zero ordine, doar alerte. "
                         "Sigur in paralel cu botul de pe server.")
    ap.add_argument("--once", action="store_true", help="(cu --watch) o singura verificare si iese")
    ap.add_argument("--close", action="store_true",
                    help="IESIRE: vinde TOT spot-ul + acopera TOT short-ul (flat), apoi iese. "
                         "Real daca STRAT_EXECUTE=true (altfel/--paper = simulare). "
                         "OPRESTE intai botul+watchdog (vezi dn_close.sh) ca sa nu se bata cu el.")
    args = ap.parse_args()
    if args.watch:
        single_instance("dn_watch")            # separate watcher process uses its own lock
    elif not (args.once or args.status or args.close):
        single_instance("dn_bot")

    dry = args.paper or not required_bool_env("STRAT_EXECUTE")
    need_wallet = not dry and not (args.funding or args.status or args.watch)
    # RESILIENCE: retry startup DNS/connection failures instead of terminating.
    while True:
        try:
            client = _client(need_wallet)
            break
        except HLError as e:
            log(f"! {e}"); return 1          # missing-key configuration error: do not retry
        except KeyboardInterrupt:
            return 130
        except Exception as e:  # noqa: BLE001 — retry after a network failure
            log(f"! conexiune esuata ({e.__class__.__name__}) — reincerc in 60s")
            time.sleep(60)
    params = DNParams.from_env(client)

    if args.funding:
        f = client.funding_rate(params.coin)
        log(f"[FUNDING] {params.coin}: {f*100:+.4f}%/ora (~{f*24*365*100:.1f}%/an)" if f is not None else "  indisponibil")
        return 0
    if args.status:
        return _cmd_status(client, params)
    if args.watch:
        return _cmd_watch(client, params, desktop=False, once=args.once)
    if args.close:
        # One-shot EXIT flattens both legs. In REAL mode, legs() reads actual balances;
        # if reading fails, do not close blindly. Reuse the existing _close implementation.
        dn = DeltaNeutral(client, params, dry_run=dry, desktop=False)
        L = dn.legs()
        if L is None:
            log("  [--close] nu pot citi picioarele (API?) — NU inchid orbeste"); return 1
        log(f"  [--close] inchid pozitia ({'PAPER (simulare)' if dry else '⚠ REAL — BANI ADEVARATI'}): "
            f"spot={L['spot_qty']:.4f} perp={L['perp_szi']:.4f}")
        dn._close(L, "inchidere manuala --close")
        dn._save()
        log("  [--close] gata — verifica rezultatul cu --status.")
        return 0

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

#!/usr/bin/env python3
"""portfolio_snapshot.py — vedere UNIFICATA cross-motor (Kraken base v2 + HL long-hold).

Monitorizare read-only: citeste starile boților + pretul curent si raporteaza pozitii,
P&L realizat + nerealizat, expunere si drawdown per-motor + total. NU atinge nimic.

  ./myenv/bin/python verify_tools/portfolio_snapshot.py            # o data (tabel)
  ./myenv/bin/python verify_tools/portfolio_snapshot.py --json     # linie JSON (pt cron/log)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRAKEN_DIR = os.path.join(ROOT, "kraken")
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# perechi Kraken monitorizate (pair -> eticheta, e_paper)
KRAKEN_PAIRS = [("HYPEUSD", "HYPE", False), ("TAOUSD", "TAO", False), ("ADAUSD", "ADA", True)]


def _load_state(pair: str) -> dict | None:
    path = os.path.join(KRAKEN_DIR, f".state_{pair}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _kraken_price(pair: str):
    try:
        sys.path.insert(0, KRAKEN_DIR)
        from kraken_client import KrakenClient
        return KrakenClient().last_price(pair)
    except Exception:  # noqa: BLE001 — snapshot nu trebuie sa moara pe un pret ratat
        return None


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def kraken_rows() -> list[dict]:
    rows = []
    for pair, label, paper in KRAKEN_PAIRS:
        s = _load_state(pair)
        if s is None:
            continue
        qty = _f(s.get("qty"))
        cost = _f(s.get("cost"))
        px = _kraken_price(pair) or 0.0
        upnl = (px - cost / qty) * qty if qty > 1e-12 and px else 0.0
        rows.append({
            "engine": f"Kraken {label}" + (" (paper)" if paper else ""),
            "paper": paper,
            "qty": round(qty, 6),
            "value_usd": round(qty * px, 2),
            "realized_net": round(_f(s.get("realized_net")), 2),
            "unrealized": round(upnl, 2),
            "spent": round(_f(s.get("spent")), 0),
            "cycle": s.get("cycle"),
            "dca_buys": s.get("dca_buys"),
            "open_orders": len(s.get("orders", [])),
        })
    return rows


def hl_row() -> dict | None:
    try:
        sys.path.insert(0, os.path.join(ROOT, "hyperliquid"))
        from common import load_dotenv
        load_dotenv(os.path.join(ROOT, "hyperliquid", ".env"))
        load_dotenv(os.path.join(ROOT, "hyperliquid", "config.env"))
        from hl_client import HLClient
        addr = os.environ.get("HL_ACCOUNT_ADDRESS")
        c = HLClient(secret_key=None, account_address=addr, mainnet=True)
        ss = c.info.spot_user_state(addr)
        mid = c.spot_mid(c.resolve_spot_pair("HYPE")) or 0.0
        hype = next((float(b["total"]) for b in ss.get("balances", []) if b.get("coin") == "HYPE"), 0.0)
        usdc = next((float(b["total"]) for b in ss.get("balances", []) if b.get("coin") == "USDC"), 0.0)
        return {"engine": "HL HYPE (long-hold)", "paper": False, "qty": round(hype, 4),
                "value_usd": round(hype * mid, 2), "cash_usd": round(usdc, 2)}
    except Exception as e:  # noqa: BLE001
        return {"engine": "HL", "error": str(e)[:60]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot portofoliu cross-motor (read-only).")
    ap.add_argument("--json", action="store_true", help="o linie JSON (pt cron/log)")
    args = ap.parse_args()

    kr = kraken_rows()
    hl = hl_row()
    real_total = sum(r["realized_net"] for r in kr if not r["paper"])
    upnl_total = sum(r["unrealized"] for r in kr if not r["paper"])
    snap = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kraken": kr, "hl": hl,
            "kraken_real_realized_net": round(real_total, 2),
            "kraken_real_unrealized": round(upnl_total, 2)}

    if args.json:
        print(json.dumps(snap))
        return 0

    print(f"=== PORTOFOLIU {snap['ts']} ===")
    print(f"  {'motor':<22}{'qty':>12}{'valoare$':>11}{'realizat$':>11}{'nereal$':>10}{'buget':>10}")
    for r in kr:
        print(f"  {r['engine']:<22}{r['qty']:>12.4f}{r['value_usd']:>11.2f}"
              f"{r['realized_net']:>11.2f}{r['unrealized']:>10.2f}{str(r['spent'])+'':>10}")
    if hl and "error" not in hl:
        print(f"  {hl['engine']:<22}{hl['qty']:>12.4f}{hl['value_usd']:>11.2f}"
              f"{'—':>11}{'—':>10}  cash ${hl.get('cash_usd', 0):.0f}")
    elif hl:
        print(f"  HL: eroare ({hl['error']})")
    print(f"  --- Kraken REAL: realizat ${real_total:+.2f} | nerealizat ${upnl_total:+.2f} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

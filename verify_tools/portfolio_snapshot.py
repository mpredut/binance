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
        # drawdown nerealizat ca % din cost (declanseaza risk-alert)
        dd_pct = (upnl / cost * 100.0) if cost > 1e-9 else 0.0
        rows.append({
            "engine": f"Kraken {label}" + (" (paper)" if paper else ""),
            "paper": paper,
            "qty": round(qty, 6),
            "value_usd": round(qty * px, 2),
            "cost": round(cost, 2),
            "dd_pct": round(dd_pct, 2),
            "realized_net": round(_f(s.get("realized_net")), 2),
            "unrealized": round(upnl, 2),
            "spent": round(_f(s.get("spent")), 0),
            "cycle": s.get("cycle"),
            "dca_buys": s.get("dca_buys"),
            "open_orders": len(s.get("orders", [])),
        })
    return rows


def hl_row() -> dict | None:
    import contextlib
    import io
    try:
        sys.path.insert(0, os.path.join(ROOT, "hyperliquid"))
        # load_dotenv/HLClient scriu log-uri pe stdout — le suprimam ca sa nu
        # polueze iesirea --json (jsonl de cron).
        with contextlib.redirect_stdout(io.StringIO()):
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


ALERT_STATE = os.path.join(ROOT, "logs", ".risk_alert_state.json")


def _ntfy(title: str, body: str) -> None:
    """Trimite o notificare ntfy (best-effort, fara dependinte externe)."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        # citeste din kraken/config.env daca nu e in mediu
        cfg = os.path.join(KRAKEN_DIR, "config.env")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as fh:
                for ln in fh:
                    if ln.startswith("NTFY_TOPIC="):
                        topic = ln.split("=", 1)[1].strip()
                        break
    if not topic:
        return
    import urllib.request
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "warning"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:  # noqa: BLE001 — alerta best-effort, nu blocam snapshot-ul
        pass


def _alert_state() -> dict:
    try:
        with open(ALERT_STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_alert_state(st: dict) -> None:
    try:
        with open(ALERT_STATE, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
    except OSError:
        pass


def run_alerts(kr: list[dict]) -> list[str]:
    """Verifica drawdown-ul per pozitie REALA si trimite ntfy la depasire prag.

    Prag: RISK_DD_ALERT_PCT (implicit 8%). De-dup: re-alerteaza doar daca au
    trecut >6h SAU DD-ul s-a inrautatit cu >=2pp fata de ultima alerta.
    """
    threshold = _f(os.environ.get("RISK_DD_ALERT_PCT"), 8.0)
    now = datetime.now(timezone.utc)
    st = _alert_state()
    fired = []
    for r in kr:
        if r["paper"] or r["qty"] <= 1e-9:
            continue
        dd = r["dd_pct"]  # negativ = pierdere
        if dd > -threshold:
            st.pop(r["engine"], None)  # revenit sub prag -> reseteaza
            continue
        prev = st.get(r["engine"], {})
        prev_dd = prev.get("dd", 0.0)
        prev_ts = prev.get("ts")
        stale = True
        if prev_ts:
            try:
                stale = (now - datetime.fromisoformat(prev_ts)).total_seconds() > 6 * 3600
            except ValueError:
                stale = True
        if stale or dd <= prev_dd - 2.0:
            _ntfy(
                f"⚠️ Risc {r['engine']}: DD {dd:.1f}%",
                f"{r['engine']} drawdown nerealizat {dd:.1f}% "
                f"(nereal ${r['unrealized']:.0f} pe cost ${r['cost']:.0f}, "
                f"prag {threshold:.0f}%). Verifica pozitia.")
            st[r["engine"]] = {"dd": dd, "ts": now.isoformat(timespec="seconds")}
            fired.append(f"{r['engine']} DD {dd:.1f}%")
    _save_alert_state(st)
    return fired


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot portofoliu cross-motor (read-only).")
    ap.add_argument("--json", action="store_true", help="o linie JSON (pt cron/log)")
    ap.add_argument("--alert", action="store_true",
                    help="verifica drawdown per pozitie si trimite ntfy la depasire prag")
    args = ap.parse_args()

    kr = kraken_rows()
    hl = hl_row()
    real_total = sum(r["realized_net"] for r in kr if not r["paper"])
    upnl_total = sum(r["unrealized"] for r in kr if not r["paper"])
    snap = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kraken": kr, "hl": hl,
            "kraken_real_realized_net": round(real_total, 2),
            "kraken_real_unrealized": round(upnl_total, 2)}

    if args.alert:
        fired = run_alerts(kr)
        snap["alerts_fired"] = fired

    if args.json:
        print(json.dumps(snap))
        return 0

    if args.alert:
        print(f"  risk-alert: {'; '.join(fired) if fired else 'nimic peste prag'}")

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

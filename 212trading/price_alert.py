#!/usr/bin/env python3
"""
price_alert.py — alerta ntfy cand pretul unui activ (Yahoo) trece un prag.

Generic si reutilizabil (orice simbol), prietenos cu CRON: o verificare per rulare,
cu dedup prin fisier de stare + histerezis (nu spameaza). Pretul off-hours e ultimul
close (stabil) -> nu da false alarme cand piata e inchisa.

  python3 price_alert.py RGNT --below 4
  python3 price_alert.py NVDA --above 250 --topic alt-topic
Cron (la 15 min):
  */15 * * * * cd ~/binance/212trading && python3 price_alert.py RGNT --below 4 >> price_alert.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipo_common import load_dotenv, log  # noqa: E402
from market_data import get_price_usd  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))


def push(topic: str, title: str, body: str) -> bool:
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                                 headers={"Title": title}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"  ! ntfy esuat: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Alerta ntfy la prag de pret (Yahoo).")
    ap.add_argument("symbol")
    ap.add_argument("--below", type=float, help="alerteaza cand pretul <= acest prag")
    ap.add_argument("--above", type=float, help="alerteaza cand pretul >= acest prag")
    ap.add_argument("--topic", default=None, help="topic ntfy (altfel NTFY_TOPIC din .env)")
    ap.add_argument("--state", default=None)
    ap.add_argument("--env-file", default=os.path.join(_HERE, ".env"))
    args = ap.parse_args()

    load_dotenv(args.env_file)
    # alerta de PRET -> topicul dedicat categoriei (NTFY_TOPIC_PRICE), fallback pe cel generic.
    topic = args.topic or os.environ.get("NTFY_TOPIC_PRICE") or os.environ.get("NTFY_TOPIC")
    if not topic:
        log("! niciun topic ntfy (--topic / NTFY_TOPIC_PRICE / NTFY_TOPIC in .env)"); return 1
    if args.below is None and args.above is None:
        log("! da macar --below sau --above"); return 1

    price = get_price_usd(args.symbol)
    if price is None:
        log(f"  [{args.symbol}] pret indisponibil — sar"); return 0

    state_path = args.state or os.path.join(_HERE, f".alert_{args.symbol}.json")
    st = {}
    if os.path.exists(state_path):
        try:
            st = json.load(open(state_path))
        except (OSError, ValueError):
            st = {}
    armed = st.get("armed", True)

    hit_below = args.below is not None and price <= args.below
    hit_above = args.above is not None and price >= args.above
    hit = hit_below or hit_above

    # re-armare cu histerezis 5% (ca sa nu spameze la oscilatii in jurul pragului)
    if not hit:
        if hit_below is False and args.below is not None and price > args.below * 1.05:
            armed = True
        if hit_above is False and args.above is not None and price < args.above * 0.95:
            armed = True

    if hit and armed:
        cond = f"<= {args.below}" if hit_below else f">= {args.above}"
        title = f"{args.symbol} la {price:.2f} ({cond})"
        body = f"{args.symbol} = {price:.2f} USD — prag {cond} atins. Zona de cumparare manuala."
        log(f"  [{args.symbol}] ALERTA: {price:.2f} {cond}")
        push(topic, title, body)
        armed = False
    else:
        log(f"  [{args.symbol}] pret {price:.2f} (below={args.below} above={args.above} armat={armed})")

    try:
        json.dump({"armed": armed, "last": price}, open(state_path, "w"))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

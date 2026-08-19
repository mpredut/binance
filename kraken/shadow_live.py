#!/usr/bin/env python3
"""kraken/shadow_live.py — SHADOW TEST LIVE (read-only, ZERO ordine reale, NU atinge botul).

Ruleaza 3 config-uri prin MOTORUL FAITHFUL (replay.run_replay = exact deciziile live
Strategy.step) peste ACELASI OHLC live descarcat de la Kraken, si logheaza P&L paper
comparativ. Scopul: forward-test intre configul de PRODUCTIE si 2 candidati shadow
(pre-inregistrati din cercetare) inainte de a schimba ceva pe bani reali:

  - current : configul LIVE exact (citit din config.env)                -> referinta
  - tp4     : DOAR TAKEPROFIT 5.0 -> 4.0  (+0.13pp in cercetare, fara DD in plus)
  - dca15   : DOAR DCA_DROP 1.25 -> 1.5   (+0.08pp, candidat secundar)

Determinist: dat OHLC-ul, rezultatul e reproductibil; pe masura ce fereastra Kraken
aluneca inainte, snapshot-urile periodice construiesc un time-series forward al divergentei.
Ruleaza single-shot (pt cron) sau --loop. NU citeste/scrie starea botului live.

  ./myenv/bin/python kraken/shadow_live.py                 # snapshot 60m, append JSONL
  ./myenv/bin/python kraken/shadow_live.py --interval 240  # bare de 4h
  ./myenv/bin/python kraken/shadow_live.py --loop 60       # re-ruleaza la 60 min
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

CONFIG_ENV = os.path.join(HERE, "config.env")
LOG_DIR = os.path.join(ROOT, "logs", "shadow_live")


def _load_config_env(path: str) -> None:
    """Incarca STRAT_*/KRAKEN_PAIR din config.env in os.environ (fara a suprascrie ce
    e deja setat), strip inline comments. Astfel 'current' = productia exact."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if not (key.startswith("STRAT_") or key in ("KRAKEN_PAIR", "SYMBOL_LABEL")):
                continue
            val = val.split("#", 1)[0].strip()
            os.environ.setdefault(key, val)


def _variants():
    import strategy as strat
    base = strat.StratParams.from_env()
    # Nu vrem overlay in shadow-ul asta (comparam DCA/TP clasic); dezactivez explicit.
    base = dataclasses.replace(base, trend_overlay=False)
    return {
        "current": base,
        "tp4": dataclasses.replace(base, takeprofit_pct=4.0),
        "dca15": dataclasses.replace(base, dca_drop_pct=1.5),
    }


def _fetch_with_ts(pair: str, interval: int):
    """Ca backtest.fetch_candles dar PASTREAZA timestamp-ul (pt ancorare forward).
    Intoarce [(ts_sec, open, high, low, close), ...], exclude ultima bara in formare."""
    import json as _json
    import urllib.request
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (shadow)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = _json.loads(r.read())
    if data.get("error"):
        raise RuntimeError(", ".join(data["error"]))
    res = data.get("result", {})
    key = next((k for k in res if k != "last"), None)
    if not key:
        return []
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
            for x in res[key][:-1]]


def _anchor_path(pair: str, interval: int) -> str:
    return os.path.join(LOG_DIR, f"{pair}_{interval}m.anchor")


def _get_anchor(pair: str, interval: int, default_ts: int) -> int:
    """Prima rulare fixeaza ancora = ultima bara inchisa (= forward-test de ACUM inainte).
    Rularile urmatoare o citesc, deci fereastra CRESTE, nu aluneca."""
    path = _anchor_path(pair, interval)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(default_ts))
    return default_ts


def _run_one(ohlc, params, interval, fee_pct):
    import replay as rp
    with contextlib.redirect_stdout(io.StringIO()):
        m = rp.run_replay(ohlc, params, fee_pct=fee_pct, bar_minutes=interval)
    return m


def _eval_block(ohlc4, interval, fee_pct):
    """Ruleaza cele 3 config-uri pe o lista de 4-tuple (o,h,l,c). None daca <2 bare."""
    if len(ohlc4) < 2:
        return None
    rows = {}
    for name, params in _variants().items():
        budget = float(params.max_budget)
        m = _run_one(ohlc4, params, interval, fee_pct)
        rows[name] = {
            "net_pct": round(m["net"] / budget * 100.0, 4),
            "total_pct": round(m["total"] / budget * 100.0, 4),  # inclusiv upnl deschis
            "maxdd_pct": round(m.get("max_drawdown_pct") or 0.0, 4),
            "cycles": m.get("cycles", 0),
            "open_qty": m.get("open_qty", 0.0),
        }
    return rows


def snapshot(pair: str, interval: int, fee_pct: float, quiet: bool = False) -> dict:
    bars = _fetch_with_ts(pair, interval)
    if not bars:
        raise SystemExit(f"fetch({pair},{interval}) a intors gol")
    anchor = _get_anchor(pair, interval, bars[-1][0])
    full4 = [(o, h, l, c) for (_t, o, h, l, c) in bars]
    fwd_bars = [b for b in bars if b[0] >= anchor]
    fwd4 = [(o, h, l, c) for (_t, o, h, l, c) in fwd_bars]

    def _bh(seg):
        return round((seg[-1][3] / seg[0][3] - 1.0) * 100.0, 4) if len(seg) >= 2 else None

    snap = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pair": pair,
        "interval_min": interval,
        "anchor_ts": anchor,
        "last_close": bars[-1][4],
        "window": {"bars": len(full4), "buyhold_pct": _bh(full4),
                   "configs": _eval_block(full4, interval, fee_pct)},
        "forward": {"bars": len(fwd4), "buyhold_pct": _bh(fwd4),
                    "configs": _eval_block(fwd4, interval, fee_pct)},
    }
    if not quiet:
        _print(snap)
    _append_jsonl(pair, interval, snap)
    return snap


def _print_block(title: str, blk: dict) -> None:
    r = blk.get("configs")
    bh = blk.get("buyhold_pct")
    bh_s = f"{bh:+.2f}%" if bh is not None else "n/a"
    print(f"  [{title}] {blk['bars']} bare  buy&hold {bh_s}")
    if not r:
        print("    (insuficiente bare — se acumuleaza)")
        return
    cur = r["current"]["net_pct"]
    print(f"    {'config':<9} {'net%':>8} {'total%':>8} {'maxDD%':>8} {'cicluri':>8}  vs current")
    for name in ("current", "tp4", "dca15"):
        x = r[name]
        diff = "" if name == "current" else f"{x['net_pct'] - cur:+.2f}pp"
        print(f"    {name:<9} {x['net_pct']:>8.2f} {x['total_pct']:>8.2f} "
              f"{x['maxdd_pct']:>8.2f} {x['cycles']:>8}  {diff}")


def _print(snap: dict) -> None:
    print(f"[{snap['ts']}] {snap['pair']} {snap['interval_min']}m  last={snap['last_close']}")
    _print_block("FORWARD (de la ancora)", snap["forward"])
    _print_block("window (context, fereastra completa)", snap["window"])


def _append_jsonl(pair: str, interval: int, snap: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{pair}_{interval}m.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Shadow test live: current vs tp4 vs dca15 (read-only).")
    ap.add_argument("--interval", type=int, default=60, help="minute per bara (60/240/1440)")
    ap.add_argument("--fee", type=float, default=0.26, help="comision per leg %%")
    ap.add_argument("--pair", default=None, help="implicit KRAKEN_PAIR din config.env")
    ap.add_argument("--loop", type=float, default=0.0, help="minute intre rulari (0=single-shot)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _load_config_env(CONFIG_ENV)
    pair = args.pair or os.environ.get("KRAKEN_PAIR", "HYPEUSD")

    while True:
        try:
            snapshot(pair, args.interval, args.fee, quiet=args.quiet)
        except Exception as e:  # shadow-ul nu trebuie sa moara pe un fetch ratat
            print(f"[shadow_live] eroare: {e}", file=sys.stderr)
        if args.loop <= 0:
            break
        time.sleep(args.loop * 60.0)


if __name__ == "__main__":
    main()

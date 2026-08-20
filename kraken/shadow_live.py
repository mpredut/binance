#!/usr/bin/env python3
"""kraken/shadow_live.py — SHADOW TEST LIVE (read-only, ZERO ordine reale, NU atinge botul).

Ruleaza config-uri prin MOTORUL FAITHFUL (replay.run_replay = exact deciziile live
Strategy.step) peste ACELASI OHLC live descarcat de la Kraken, si logheaza P&L paper
comparativ. Scopul: forward-test intre configul de PRODUCTIE si candidați shadow
(pre-inregistrati din cercetare) inainte de a schimba ceva pe bani reali:

  - current : configul LIVE exact (citit din .env apoi config.env)      -> referinta
  - tp4     : DOAR TAKEPROFIT 5.0 -> 4.0  (+0.13pp in cercetare, fara DD in plus)
  - dca15   : DOAR DCA_DROP 1.25 -> 1.5   (+0.08pp, candidat secundar)
  - dca_progressive025: primul DCA la 1.25%, apoi +0.25pp/treaptă;
    candidat de risc, central neutru și mai bun sub stress în benchmark
  - dca_vol_m1 (doar 240m): sumă DCA scalată cu volatilitatea OHLC; reduce
    tail/DD, dar pierde majoritatea ferestrelor active și rămâne defensiv
  - overlay650t8 (doar 240m): overlay cu top-up 650 si trail 8%; candidat
    EXPLORATORIU pentru forward, neaprobat pentru live

Determinist: dat OHLC-ul, rezultatul e reproductibil. Barele forward închise sunt
păstrate local, astfel încât fereastra ancorată crește și după limita Kraken de 720 bare.
Ruleaza single-shot (pt cron) sau --loop. NU citeste/scrie starea botului live.

  ./myenv/bin/python kraken/shadow_live.py                 # snapshot 60m, append JSONL
  ./myenv/bin/python kraken/shadow_live.py --interval 240  # bare de 4h
  ./myenv/bin/python kraken/shadow_live.py --loop 60       # re-ruleaza la 60 min
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import difflib
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
DEFAULT_ENV = os.path.join(HERE, ".env")
LOG_DIR = os.path.join(ROOT, "logs", "shadow_live")


def _load_runtime_config(env_path: str | None = None,
                         config_path: str | None = None) -> None:
    """Reproduce exact ordinea din kraken_bot: .env are prioritate, config completează."""
    from kraken_common import load_dotenv
    env_path = env_path or os.environ.get("ENV_FILE", DEFAULT_ENV)
    config_path = config_path or os.path.join(os.path.dirname(env_path) or ".", "config.env")
    load_dotenv(env_path)
    load_dotenv(config_path)


def _variants(interval: int):
    from strategies import spot_dca as strat
    base = strat.StratParams.from_env()
    variants = {
        "current": base,
        "tp4": dataclasses.replace(base, takeprofit_pct=4.0),
        "dca15": dataclasses.replace(base, dca_drop_pct=1.5),
        "dca_progressive025": dataclasses.replace(
            base, dca_spacing_growth_pct=0.25,
        ),
    }
    # A folosește OHLC fix pentru aceeași cadență live/replay; nu îl rulăm pe alt interval.
    if interval == base.tp_trail_vol_interval:
        variants["A_trail"] = dataclasses.replace(
            base,
            tp_trail_adaptive=True,
            tp_trail_k=2.0,
            tp_trail_min=1.5,
            tp_trail_max=8.0,
        )
    # Sizing-ul DCA folosește OHLC fix. Reduce tail-ul istoric, dar nu trece
    # gate-ul de randament/pairwise, deci rămâne strict observațional.
    if interval == base.dca_vol_interval:
        variants["dca_vol_m1"] = dataclasses.replace(
            base,
            dca_vol_scale_k=-1.0,
            dca_vol_ref=2.0,
        )
    # Overlay-ul folosește semnal OHLC de 240m; nu îl simulăm artificial pe 60m.
    # Valorile sunt preînregistrate după analiza istorică și rămân fixe în forward.
    if interval == base.trend_interval:
        variants["overlay650t8"] = dataclasses.replace(
            base,
            trend_overlay=True,
            trend_topup=650.0,
            trend_trail_pct=8.0,
            trend_exit_break=False,
        )
    # B: frana-DCA in downtrend confirmat. Reduce tail-ul/DD, dar benchmarkul financiar
    # central/stress arată un sacrificiu consistent de randament -> doar observațional.
    # Foloseste semnalul de trend pe OHLC fix, ca A -> doar pe intervalul de trend.
    if interval == base.trend_interval:
        variants["B_dcabrake"] = dataclasses.replace(
            base,
            dca_trend_brake=True,
            dca_brake_min_pct=1.5,
        )
    return variants


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


def _history_path(pair: str, interval: int) -> str:
    return os.path.join(LOG_DIR, f"{pair}_{interval}m.ohlc.json")


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


def _load_history(pair: str, interval: int) -> list[tuple[int, float, float, float, float]]:
    path = _history_path(pair, interval)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return [(int(row[0]), *(float(value) for value in row[1:])) for row in rows]


def _save_history(pair: str, interval: int, bars) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = _history_path(pair, interval)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(bars, fh, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp_path, path)


def _merge_forward_history(pair: str, interval: int, anchor: int, fetched):
    """Unește barele păstrate cu fetch-ul curent și detectează pierderea de istoric."""
    cached = [bar for bar in _load_history(pair, interval) if bar[0] >= anchor]
    fetched = [bar for bar in fetched if bar[0] >= anchor]

    if cached and fetched:
        expected_step = interval * 60
        if cached[-1][0] < fetched[0][0] - expected_step:
            raise RuntimeError(
                "istoricul forward are un gol mai mare decât un interval; "
                "nu pot raporta o fereastră ancorată completă"
            )

    merged_by_ts = {bar[0]: bar for bar in cached}
    merged_by_ts.update({bar[0]: bar for bar in fetched})
    merged = [merged_by_ts[ts] for ts in sorted(merged_by_ts)]
    if not merged or merged[0][0] != anchor:
        raise RuntimeError(
            f"ancora forward {anchor} nu mai este disponibilă în istoricul local/Kraken"
        )
    _save_history(pair, interval, merged)
    return merged


def _run_one(ohlc, params, interval, fee_pct, *, include_decision_trace=False):
    import replay as rp
    with contextlib.redirect_stdout(io.StringIO()):
        m = rp.run_replay(
            ohlc, params, fee_pct=fee_pct, bar_minutes=interval,
            include_decision_trace=include_decision_trace,
        )
    return m


def _decision_distance(reference: list[dict], candidate: list[dict]) -> int:
    """Numără evenimentele de ordin inserate/șterse/înlocuite față de current."""
    def fingerprint(event):
        return tuple(sorted(event.items()))

    matcher = difflib.SequenceMatcher(
        a=[fingerprint(event) for event in reference],
        b=[fingerprint(event) for event in candidate],
        autojunk=False,
    )
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def _eval_block(ohlc4, interval, fee_pct, *, include_decision_trace=False):
    """Rulează toate variantele pe OHLC; întoarce None dacă sunt sub două bare."""
    if len(ohlc4) < 2:
        return None
    rows = {}
    for name, params in _variants(interval).items():
        budget = float(params.max_budget)
        m = _run_one(
            ohlc4, params, interval, fee_pct,
            include_decision_trace=include_decision_trace,
        )
        rows[name] = {
            "net_pct": round(m["net"] / budget * 100.0, 4),
            "total_pct": round(m["total"] / budget * 100.0, 4),  # inclusiv upnl deschis
            "maxdd_pct": round(m.get("max_drawdown_pct") or 0.0, 4),
            "cycles": m.get("cycles", 0),
            "open_qty": m.get("open_qty", 0.0),
        }
        if include_decision_trace:
            rows[name]["decision_trace"] = m.get("decision_trace", [])
    if include_decision_trace:
        reference = rows["current"]["decision_trace"]
        for name, row in rows.items():
            row["decision_divergences"] = (
                0 if name == "current" else
                _decision_distance(reference, row["decision_trace"])
            )
    return rows


def snapshot(pair: str, interval: int, fee_pct: float, quiet: bool = False) -> dict:
    bars = _fetch_with_ts(pair, interval)
    if not bars:
        raise RuntimeError(f"fetch({pair},{interval}) a intors gol")
    anchor = _get_anchor(pair, interval, bars[-1][0])
    full4 = [(o, h, l, c) for (_t, o, h, l, c) in bars]
    fwd_bars = _merge_forward_history(pair, interval, anchor, bars)
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
                    "configs": _eval_block(
                        fwd4, interval, fee_pct, include_decision_trace=True,
                    )},
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
    cur = r["current"]["total_pct"]
    print(f"    {'config':<20} {'net%':>8} {'total%':>8} {'maxDD%':>8} "
          f"{'cicluri':>8} {'Δdec':>6}  vs current total")
    for name, x in r.items():
        diff = "" if name == "current" else f"{x['total_pct'] - cur:+.2f}pp"
        divergences = x.get("decision_divergences")
        divergence_text = "-" if divergences is None else str(divergences)
        print(f"    {name:<20} {x['net_pct']:>8.2f} {x['total_pct']:>8.2f} "
              f"{x['maxdd_pct']:>8.2f} {x['cycles']:>8} "
              f"{divergence_text:>6}  {diff}")


def _print(snap: dict) -> None:
    print(f"[{snap['ts']}] {snap['pair']} {snap['interval_min']}m  last={snap['last_close']}")
    _print_block("FORWARD (de la ancora)", snap["forward"])
    _print_block("window (context, fereastra completa)", snap["window"])


def _append_jsonl(pair: str, interval: int, snap: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{pair}_{interval}m.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Shadow test live: current vs candidați preînregistrați (read-only)."
    )
    ap.add_argument("--interval", type=int, default=60, help="minute per bara (60/240/1440)")
    ap.add_argument("--fee", type=float, default=0.26, help="comision per leg %%")
    ap.add_argument("--pair", default=None, help="implicit KRAKEN_PAIR din .env/config.env")
    ap.add_argument("--loop", type=float, default=0.0, help="minute intre rulari (0=single-shot)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _load_runtime_config()
    pair = args.pair or os.environ.get("KRAKEN_PAIR", "HYPEUSD")

    while True:
        try:
            snapshot(pair, args.interval, args.fee, quiet=args.quiet)
        except Exception as e:  # în loop, un fetch ratat nu oprește monitorizarea
            print(f"[shadow_live] eroare: {e}", file=sys.stderr)
            if args.loop <= 0:
                return 1
        if args.loop <= 0:
            return 0
        time.sleep(args.loop * 60.0)


if __name__ == "__main__":
    raise SystemExit(main())

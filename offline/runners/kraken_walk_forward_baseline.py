#!/usr/bin/env python3
"""Baseline walk-forward pentru configurația Kraken live, fără optimizare.

Încarcă aceeași configurație ca botul (.env apoi config.env), rulează motorul
faithful din kraken/replay.py pe ferestre temporale fără shuffle și păstrează
dataset-ul exact folosit împreună cu hash-ul lui. Fiecare segment pornește cu
stare curată; TEST este raportul out-of-sample, TRAIN/VALIDATION sunt doar
diagnostic de regim și nu aleg parametri.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
KRAKEN_DIR = ROOT / "kraken"
sys.path.insert(0, str(KRAKEN_DIR))
sys.path.insert(0, str(ROOT))

from kraken_common import load_dotenv  # noqa: E402
import replay as kraken_replay  # noqa: E402
from strategy import StratParams  # noqa: E402
from offline.backtests.walk_forward import walk_forward_splits  # noqa: E402


def fetch_closed_candles(pair: str, interval: int) -> list[dict]:
    """Citește maximum 720 bare Kraken și elimină ultima bară încă în formare."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "binance-repo/walk-forward-baseline"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise RuntimeError(", ".join(payload["error"]))
    result = payload.get("result", {})
    key = next((name for name in result if name != "last"), None)
    rows = result.get(key, []) if key else []
    return [
        {
            "timestamp": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in rows[:-1]
    ]


def _canonical_bytes(records: list[dict]) -> bytes:
    lines = ["timestamp,open,high,low,close"]
    for row in records:
        lines.append(
            f"{int(row['timestamp'])},{row['open']:.12g},{row['high']:.12g},"
            f"{row['low']:.12g},{row['close']:.12g}"
        )
    return ("\n".join(lines) + "\n").encode("ascii")


def dataset_sha256(records: list[dict]) -> str:
    return hashlib.sha256(_canonical_bytes(records)).hexdigest()


def save_frozen_dataset(records: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="ascii", newline="") as handle:
        handle.write(_canonical_bytes(records).decode("ascii"))


def load_frozen_dataset(source: Path) -> list[dict]:
    with source.open("r", encoding="ascii", newline="") as handle:
        return [
            {
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for row in csv.DictReader(handle)
        ]


def _iso(timestamp: int) -> str:
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()


def _ohlc(records: list[dict]) -> list[tuple[float, float, float, float]]:
    return [(row["open"], row["high"], row["low"], row["close"]) for row in records]


def _segment_result(records: list[dict], params: StratParams, fee_pct: float,
                    interval: int) -> dict:
    original_log = kraken_replay._strat.log
    kraken_replay._strat.log = lambda *_args, **_kwargs: None
    try:
        metrics = kraken_replay.run_replay(
            _ohlc(records), params, fee_pct=fee_pct, bar_minutes=interval,
        )
    finally:
        kraken_replay._strat.log = original_log
    buy_hold_pct = (
        (records[-1]["close"] / records[0]["close"] - 1.0) * 100.0
        if records[0]["close"] > 0 else None
    )
    return {
        "start_utc": _iso(records[0]["timestamp"]),
        "end_utc": _iso(records[-1]["timestamp"]),
        "bars": len(records),
        "buy_hold_return_pct": buy_hold_pct,
        "metrics": metrics,
    }


def _optional_min(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def evaluate_walk_forward(records: list[dict], params: StratParams, *, fee_pct: float,
                          interval: int, train_size: int, validation_size: int,
                          test_size: int, step_size: int) -> dict:
    folds = walk_forward_splits(
        len(records), train_size=train_size, validation_size=validation_size,
        test_size=test_size, step_size=step_size,
    )
    if not folds:
        raise ValueError(
            f"date insuficiente: {len(records)} bare pentru "
            f"train={train_size}, validation={validation_size}, test={test_size}"
        )

    fold_results = []
    for index, fold in enumerate(folds, start=1):
        fold_results.append({
            "fold": index,
            "train": _segment_result(records[fold.train], params, fee_pct, interval),
            "validation": _segment_result(
                records[fold.validation], params, fee_pct, interval,
            ),
            "test": _segment_result(records[fold.test], params, fee_pct, interval),
        })

    tests = [fold["test"] for fold in fold_results]
    returns = [fold["metrics"]["return_pct"] for fold in tests]
    compounded = 100.0
    for value in returns:
        compounded *= 1.0 + value / 100.0
    return {
        "folds": fold_results,
        "aggregate_test": {
            "fold_count": len(tests),
            "mean_return_pct": statistics.fmean(returns),
            "median_return_pct": statistics.median(returns),
            "worst_return_pct": min(returns),
            "compounded_reset_return_pct": compounded - 100.0,
            "worst_max_drawdown_pct": max(
                fold["metrics"]["max_drawdown_pct"] for fold in tests
            ),
            "worst_sortino": _optional_min(
                [fold["metrics"]["sortino"] for fold in tests]
            ),
            "mean_buy_hold_return_pct": statistics.fmean(
                fold["buy_hold_return_pct"] for fold in tests
            ),
            "total_cycles": sum(fold["metrics"]["cycles"] for fold in tests),
            "total_fills": sum(fold["metrics"]["fills"] for fold in tests),
        },
    }


def automatic_window_sizes(sample_count: int) -> tuple[int, int, int, int]:
    """Alege trei fold-uri aproximative, proporțional cu istoricul disponibil."""
    if sample_count < 20:
        raise ValueError("sunt necesare minimum 20 de bare pentru ferestre automate")
    train = max(1, int(sample_count * 0.45))
    validation = max(1, int(sample_count * 0.10))
    test = max(1, int(sample_count * 0.15))
    return train, validation, test, test


def _parse_intervals(value: str) -> list[int]:
    intervals = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not intervals or any(interval <= 0 for interval in intervals):
        raise argparse.ArgumentTypeError("intervalele trebuie să fie minute pozitive")
    return intervals


def _parse_dataset_specs(specs: list[str]) -> dict[int, Path]:
    result = {}
    for spec in specs:
        try:
            interval, path = spec.split("=", 1)
            result[int(interval)] = Path(path).expanduser().resolve()
        except (ValueError, OSError) as exc:
            raise ValueError(f"dataset invalid {spec!r}; formatul este INTERVAL=CALE") from exc
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(KRAKEN_DIR / ".env"))
    parser.add_argument("--config-file", default=str(KRAKEN_DIR / "config.env"))
    parser.add_argument("--pair", help="implicit KRAKEN_PAIR din configurația live")
    parser.add_argument("--intervals", type=_parse_intervals, default=[60, 240, 1440])
    parser.add_argument("--fee", type=float, default=0.26, help="fee per leg, procente")
    parser.add_argument("--train", type=int, help="bare; implicit auto 45%% din istoric")
    parser.add_argument("--validation", type=int, help="bare; implicit auto 10%%")
    parser.add_argument("--test", type=int, help="bare; implicit auto 15%%")
    parser.add_argument("--step", type=int, help="bare; implicit egal cu TEST")
    parser.add_argument(
        "--dataset", action="append", default=[], metavar="INTERVAL=CALE",
        help="folosește un CSV înghețat în loc de API; repetabil pentru fiecare interval",
    )
    parser.add_argument(
        "--output-dir", default=str(ROOT / "offline" / "results" / "kraken_walk_forward")
    )
    args = parser.parse_args()

    if args.fee < 0:
        parser.error("--fee nu poate fi negativ")
    explicit_windows = (args.train, args.validation, args.test)
    if any(value is not None for value in explicit_windows) and not all(
            value is not None for value in explicit_windows):
        parser.error("--train, --validation și --test se dau împreună sau deloc")
    if any(value is not None and value <= 0
           for value in (*explicit_windows, args.step)):
        parser.error("dimensiunile walk-forward trebuie să fie pozitive")

    # Aceeași ordine ca kraken_bot.py: .env are prioritate, config.env completează.
    load_dotenv(args.env_file)
    load_dotenv(args.config_file)
    pair = (args.pair or os.environ.get("KRAKEN_PAIR") or "").strip().upper()
    if not pair:
        parser.error("pereche lipsă: --pair sau KRAKEN_PAIR")
    params = StratParams.from_env()
    supplied = _parse_dataset_specs(args.dataset)
    output_dir = Path(args.output_dir).expanduser().resolve()
    dataset_dir = output_dir / "datasets"
    generated_at = dt.datetime.now(tz=dt.timezone.utc)

    report = {
        "schema_version": 1,
        "generated_at_utc": generated_at.isoformat(),
        "purpose": "fixed-live-config walk-forward baseline; no parameter selection",
        "runtime": {
            "pair": pair,
            "strategy_mode": os.environ.get("STRATEGY_MODE", "avg_tp"),
            "execute_live": os.environ.get("STRAT_EXECUTE", "false").lower() == "true",
        },
        "strategy_params": dataclasses.asdict(params),
        "fee_pct_per_leg": args.fee,
        "walk_forward": {
            "requested_sizes": (
                {"train": args.train, "validation": args.validation,
                 "test": args.test, "step": args.step}
                if args.train is not None else "auto: 45%/10%/15%, step=test"
            ),
            "shuffle": False,
            "segment_state": "reset",
            "selection": "none; current live config is held fixed",
        },
        "intervals": {},
    }

    for interval in args.intervals:
        source = supplied.get(interval)
        records = load_frozen_dataset(source) if source else fetch_closed_candles(pair, interval)
        if not records:
            raise RuntimeError(f"dataset gol pentru {pair} {interval}m")
        digest = dataset_sha256(records)
        frozen_path = dataset_dir / f"{pair}_{interval}m_{digest[:12]}.csv"
        save_frozen_dataset(records, frozen_path)
        if args.train is None:
            train_size, validation_size, test_size, step_size = automatic_window_sizes(
                len(records)
            )
        else:
            train_size, validation_size, test_size = (
                args.train, args.validation, args.test,
            )
            step_size = args.step or test_size
        result = evaluate_walk_forward(
            records, params, fee_pct=args.fee, interval=interval,
            train_size=train_size, validation_size=validation_size,
            test_size=test_size, step_size=step_size,
        )
        report["intervals"][str(interval)] = {
            "dataset": {
                "source": str(source) if source else "Kraken public OHLC",
                "frozen_file": str(frozen_path),
                "sha256": digest,
                "bars": len(records),
                "start_utc": _iso(records[0]["timestamp"]),
                "end_utc": _iso(records[-1]["timestamp"]),
            },
            "window_sizes": {
                "train": train_size, "validation": validation_size,
                "test": test_size, "step": step_size,
            },
            **result,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = output_dir / f"baseline_{pair}_{stamp}.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)

    print(f"Baseline salvat: {report_path}")
    for interval, value in report["intervals"].items():
        aggregate = value["aggregate_test"]
        print(
            f"{interval}m | folds={aggregate['fold_count']} "
            f"mean={aggregate['mean_return_pct']:+.3f}% "
            f"worst={aggregate['worst_return_pct']:+.3f}% "
            f"worstDD={aggregate['worst_max_drawdown_pct']:.3f}% "
            f"cycles={aggregate['total_cycles']} fills={aggregate['total_fills']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

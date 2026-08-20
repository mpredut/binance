#!/usr/bin/env python3
"""Generează un raport read-only din fișierele execution_audit JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from offline.backtests.execution_calibration import (  # noqa: E402
    calibrate_execution_events,
)


def _paths(inputs: list[Path]) -> list[Path]:
    paths = []
    for path in inputs:
        resolved = path.expanduser().resolve()
        if resolved.is_dir():
            paths.extend(sorted(resolved.glob("execution_audit_*.jsonl")))
        else:
            paths.append(resolved)
    return sorted(set(paths))


def _load(paths: list[Path], venue: str | None) -> list[dict]:
    events = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: JSON invalid") from error
                if venue and str(event.get("venue") or "").lower() != venue.lower():
                    continue
                events.append(event)
    return events


def _fmt(value) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def markdown_report(report: dict, paths: list[Path]) -> str:
    lines = [
        "# Execution audit calibration", "",
        f"Fișiere: {len(paths)}", "",
        "| Tip | Orders | Filled | Partial | Fee p50 bps | Fee p95 bps | "
        "Latency p50 s | Fill ratio p50 | Shortfall p50 bps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("all", "limit", "market"):
        item = report["summary"][name]
        lines.append(
            f"| {name} | {item['orders']} | {item['filled']} | "
            f"{item['ever_partial']} | {_fmt(item['fee_bps']['p50'])} | "
            f"{_fmt(item['fee_bps']['p95'])} | "
            f"{_fmt(item['first_fill_latency_s']['p50'])} | "
            f"{_fmt(item['final_fill_ratio']['p50'])} | "
            f"{_fmt(item['market_execution_shortfall_bps']['p50'])} |"
        )
    ready = report["calibration_readiness"]
    lines.extend([
        "", "## Readiness", "",
        f"Filled orders: {ready['filled_orders']}/{ready['minimum_filled_orders']} minimum.",
        "", "MARKET shortfall: disponibil după deploy când auditul conține "
        "`reference_price`; nu este slippage pur.",
        "", "Market slippage separat: indisponibil — "
        + ready["market_slippage_blocker"] + ".",
        "", "Spread: indisponibil — " + ready["spread_blocker"] + ".", "",
        "Raportul este observațional și nu modifică automat scenariile de backtest.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="+", type=Path,
        help="fișiere JSONL sau directoare logger/execution_audit",
    )
    parser.add_argument("--venue")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    paths = _paths(args.inputs)
    if not paths:
        parser.error("nu am găsit fișiere execution_audit")
    try:
        events = _load(paths, args.venue)
    except ValueError as error:
        parser.error(str(error))
    report = calibrate_execution_events(events)
    report["source_files"] = [str(path) for path in paths]
    output = args.output.expanduser().resolve() if args.output else None
    markdown = args.markdown.expanduser().resolve() if args.markdown else None
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    if markdown:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        with markdown.open("w", encoding="utf-8") as handle:
            handle.write(markdown_report(report, paths))
    print(markdown_report(report, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

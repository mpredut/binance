#!/usr/bin/env python3
"""Comparație robustă one-factor pentru configurația Kraken live.

Consumă raportul și CSV-urile înghețate de ``kraken_walk_forward_baseline.py``.
Nu caută combinații și nu schimbă live config: ablațiile modifică un singur
mecanism. Singura combinație este o confirmare predefinită a celor două
ablații fără pierderi, nu un grid căutat după rezultat.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from pathlib import Path
import sys


RUNNER_DIR = Path(__file__).resolve().parent
ROOT = RUNNER_DIR.parents[1]
sys.path.insert(0, str(RUNNER_DIR))
sys.path.insert(0, str(ROOT))

import kraken_walk_forward_baseline as baseline  # noqa: E402
from offline.backtests.walk_forward import (  # noqa: E402
    summarize_test_windows,
    walk_forward_splits,
)


@dataclasses.dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    overrides: dict


def default_candidates() -> list[Candidate]:
    """Vecinătate mică, interpretabilă, în jurul configurației live."""
    return [
        Candidate("live", "configurația live neschimbată", {}),
        Candidate("classic_tp", "TP fix; trailing oprit", {"tp_trend_hold": False}),
        Candidate("trail_2", "trailing 2%", {"tp_trail_pct": 2.0}),
        Candidate("trail_4", "trailing 4%", {"tp_trail_pct": 4.0}),
        Candidate("trail_5", "trailing 5%", {"tp_trail_pct": 5.0}),
        Candidate("tp_4", "prag TP 4%", {"takeprofit_pct": 4.0}),
        Candidate("tp_6", "prag TP 6%", {"takeprofit_pct": 6.0}),
        Candidate("dca_drop_1", "DCA la scădere 1%", {"dca_drop_pct": 1.0}),
        Candidate("dca_drop_1_5", "DCA la scădere 1,5%", {"dca_drop_pct": 1.5}),
        Candidate("dca_drop_2", "DCA la scădere 2%", {"dca_drop_pct": 2.0}),
        Candidate("reentry_1_5", "reintrare după -1,5%", {"reentry_drop_pct": 1.5}),
        Candidate("reentry_3", "reintrare după -3%", {"reentry_drop_pct": 3.0}),
        Candidate("sl_off", "stop-loss oprit", {"stop_loss_pct": 0.0}),
        Candidate("sl_10", "stop-loss 10%", {"stop_loss_pct": 10.0}),
        Candidate("sl_15", "stop-loss 15%", {"stop_loss_pct": 15.0}),
        Candidate("max_dca_6", "maximum 6 DCA", {"max_dca_buys": 6}),
        Candidate(
            "lower_sizing", "entry 500 + DCA 250 USD",
            {"entry_amount": 500.0, "dca_amount": 250.0},
        ),
        Candidate(
            "confirm_dca_1_5_trail_2",
            "confirmare: DCA 1,5% + trailing 2%",
            {"dca_drop_pct": 1.5, "tp_trail_pct": 2.0},
        ),
    ]


def hype_240_candidates() -> list[Candidate]:
    """Set preînregistrat pentru datasetul lung HYPE de 240m."""
    return [
        Candidate("live", "configurația live neschimbată", {}),
        Candidate(
            "overlay_orig", "overlay original: top-up 2000, trail 5%",
            {"trend_overlay": True, "trend_topup": 2000.0,
             "trend_trail_pct": 5.0, "trend_interval": 240},
        ),
        Candidate(
            "overlay650t8", "overlay redus: top-up 650, trail 8%",
            {"trend_overlay": True, "trend_topup": 650.0,
             "trend_trail_pct": 8.0, "trend_interval": 240},
        ),
        Candidate(
            "A_adaptive_trail", "trailing adaptiv k=2, clamp 1,5-8%",
            {"tp_trail_adaptive": True, "tp_trail_k": 2.0,
             "tp_trail_min": 1.5, "tp_trail_max": 8.0,
             "tp_trail_vol_interval": 240},
        ),
        Candidate(
            "B_dca_brake", "blochează DCA în downtrend confirmat",
            {"dca_trend_brake": True, "dca_brake_min_pct": 1.5,
             "trend_interval": 240},
        ),
        Candidate("tp_4", "prag TP 4%", {"takeprofit_pct": 4.0}),
        Candidate("dca_drop_1_5", "DCA la scădere 1,5%", {"dca_drop_pct": 1.5}),
    ]


def _load_report(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        report = json.load(handle)
    if report.get("schema_version") != 1 or not report.get("intervals"):
        raise ValueError("raport baseline incompatibil sau gol")
    return report


def _load_datasets(report: dict) -> dict[int, tuple[list[dict], dict, int]]:
    datasets = {}
    for interval_text, section in report["intervals"].items():
        interval = int(interval_text)
        path = Path(section["dataset"]["frozen_file"])
        records = baseline.load_frozen_dataset(path)
        actual_hash = baseline.dataset_sha256(records)
        expected_hash = section["dataset"]["sha256"]
        if actual_hash != expected_hash:
            raise ValueError(
                f"hash dataset diferit pentru {interval}m: {actual_hash} != {expected_hash}"
            )
        datasets[interval] = (
            records,
            section["window_sizes"],
            int(report.get("walk_forward", {}).get("signal_warmup_bars", 0)),
        )
    return datasets


def _candidate_windows(datasets: dict, params, fee_pct: float, execution) -> list[dict]:
    windows = []
    for interval, (records, sizes, warmup_bars) in sorted(datasets.items()):
        folds = walk_forward_splits(
            len(records), train_size=sizes["train"],
            validation_size=sizes["validation"], test_size=sizes["test"],
            step_size=sizes["step"],
        )
        for fold_index, fold in enumerate(folds, start=1):
            warmup = records[max(0, fold.test.start - warmup_bars):fold.test.start]
            segment = baseline._segment_result(
                records[fold.test], params, fee_pct, interval, warmup,
                execution=execution,
            )
            metrics = segment["metrics"]
            windows.append({
                "key": f"{interval}m/fold-{fold_index}",
                "interval": interval,
                "fold": fold_index,
                "start_utc": segment["start_utc"],
                "end_utc": segment["end_utc"],
                "return_pct": metrics["return_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "buy_hold_return_pct": segment["buy_hold_return_pct"],
                "cycles": metrics["cycles"],
                "fills": metrics["fills"],
            })
    return windows


def _relative(candidate: list[dict], live: list[dict]) -> dict:
    live_by_key = {window["key"]: window for window in live}
    deltas = [
        window["return_pct"] - live_by_key[window["key"]]["return_pct"]
        for window in candidate
    ]
    tolerance = 1e-10
    return {
        "mean_return_delta_pct": sum(deltas) / len(deltas),
        "wins_vs_live": sum(delta > tolerance for delta in deltas),
        "ties_vs_live": sum(abs(delta) <= tolerance for delta in deltas),
        "losses_vs_live": sum(delta < -tolerance for delta in deltas),
    }


def _dominates(left: dict, right: dict, tolerance: float = 1e-10) -> bool:
    """Dominanță conservatoare: medie și worst-case mai bune, DD nu mai mare."""
    no_worse = (
        left["mean_return_pct"] >= right["mean_return_pct"] - tolerance
        and left["worst_return_pct"] >= right["worst_return_pct"] - tolerance
        and left["worst_max_drawdown_pct"] <= (
            right["worst_max_drawdown_pct"] + tolerance
        )
    )
    strictly_better = (
        left["mean_return_pct"] > right["mean_return_pct"] + tolerance
        or left["worst_return_pct"] > right["worst_return_pct"] + tolerance
        or left["worst_max_drawdown_pct"] < (
            right["worst_max_drawdown_pct"] - tolerance
        )
    )
    return no_worse and strictly_better


def _pareto_names(results: dict[str, dict]) -> list[str]:
    names = []
    for name, candidate in results.items():
        if not any(
            other_name != name and _dominates(other["summary"], candidate["summary"])
            for other_name, other in results.items()
        ):
            names.append(name)
    return sorted(names)


def _format_number(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _markdown(report: dict) -> str:
    lines = [
        "# Kraken walk-forward robustness comparison",
        "",
        f"Generated UTC: {report['generated_at_utc']}",
        "",
        "Every candidate uses the same frozen TEST windows. No candidate is activated.",
        "",
        "| Candidate | Mean % | Median % | Worst % | Worst DD % | Positive | W/T/L vs live | Cycles |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    ordered = sorted(
        report["candidates"].items(),
        key=lambda item: (
            item[0] != "live", -item[1]["summary"]["mean_return_pct"], item[0]
        ),
    )
    for name, item in ordered:
        summary = item["summary"]
        relative = item["relative_to_live"]
        lines.append(
            f"| `{name}` | {_format_number(summary['mean_return_pct'], True)} | "
            f"{_format_number(summary['median_return_pct'], True)} | "
            f"{_format_number(summary['worst_return_pct'], True)} | "
            f"{_format_number(summary['worst_max_drawdown_pct'])} | "
            f"{summary['positive_windows']}/{summary['window_count']} | "
            f"{relative['wins_vs_live']}/{relative['ties_vs_live']}/"
            f"{relative['losses_vs_live']} | {summary['total_cycles']} |"
        )
    lines.extend([
        "",
        f"Pareto frontier: {', '.join(f'`{name}`' for name in report['pareto_frontier'])}",
        "",
        "## Fee stress — live configuration",
        "",
        "| Fee per leg % | Mean % | Worst % | Worst DD % | Positive |",
        "|---:|---:|---:|---:|---:|",
    ])
    for fee, item in report["fee_stress"].items():
        summary = item["summary"]
        lines.append(
            f"| {fee} | {_format_number(summary['mean_return_pct'], True)} | "
            f"{_format_number(summary['worst_return_pct'], True)} | "
            f"{_format_number(summary['worst_max_drawdown_pct'])} | "
            f"{summary['positive_windows']}/{summary['window_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_report", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--fee-stress", default="0.16,0.26,0.35,0.50",
        help="fee-uri per leg, procente",
    )
    parser.add_argument(
        "--candidate-set", choices=("standard", "hype-240"), default="standard",
    )
    args = parser.parse_args()

    baseline_path = args.baseline_report.expanduser().resolve()
    source_report = _load_report(baseline_path)
    datasets = _load_datasets(source_report)
    if args.candidate_set == "hype-240" and set(datasets) != {240}:
        parser.error("candidate-set hype-240 cere un baseline care conține numai intervalul 240")
    base_params = baseline.StratParams(**source_report["strategy_params"])
    base_fee = float(source_report["fee_pct_per_leg"])
    execution = baseline.ExecutionModel(**source_report.get("execution_model", {}))

    results = {}
    candidates = hype_240_candidates() if args.candidate_set == "hype-240" else default_candidates()
    for candidate in candidates:
        params = dataclasses.replace(base_params, **candidate.overrides)
        windows = _candidate_windows(datasets, params, base_fee, execution)
        results[candidate.name] = {
            "description": candidate.description,
            "overrides": candidate.overrides,
            "summary": summarize_test_windows(windows),
            "by_interval": {
                str(interval): summarize_test_windows([
                    window for window in windows if window["interval"] == interval
                ])
                for interval in sorted(datasets)
            },
            "windows": windows,
        }

    live_windows = results["live"]["windows"]
    for name, candidate in results.items():
        candidate["relative_to_live"] = _relative(candidate["windows"], live_windows)
        candidate["dominates_live"] = (
            name != "live"
            and _dominates(candidate["summary"], results["live"]["summary"])
        )

    fee_stress = {}
    for fee_text in args.fee_stress.split(","):
        fee = float(fee_text.strip())
        if fee < 0:
            parser.error("fee-ul nu poate fi negativ")
        windows = _candidate_windows(datasets, base_params, fee, execution)
        fee_stress[f"{fee:.2f}"] = {
            "summary": summarize_test_windows(windows),
            "windows": windows,
        }

    generated_at = dt.datetime.now(tz=dt.timezone.utc)
    report = {
        "schema_version": 1,
        "generated_at_utc": generated_at.isoformat(),
        "source_baseline_report": str(baseline_path),
        "method": {
            "candidate_style": (
                f"pre-registered {args.candidate_set} candidate set; no automatic selection"
            ),
            "comparison_dimensions": [
                "mean_return_pct", "worst_return_pct", "worst_max_drawdown_pct",
            ],
            "window_weighting": "equal across all timeframe/fold TEST windows",
            "execution_model": dataclasses.asdict(execution),
        },
        "candidates": results,
        "pareto_frontier": _pareto_names(results),
        "dominates_live": sorted(
            name for name, item in results.items() if item["dominates_live"]
        ),
        "fee_stress": fee_stress,
    }

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir else baseline_path.parent / "comparisons"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"comparison_{stamp}.json"
    markdown_path = output_dir / f"comparison_{stamp}.md"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write(_markdown(report))

    print(f"Comparison JSON: {json_path}")
    print(f"Comparison Markdown: {markdown_path}")
    print(f"Pareto: {', '.join(report['pareto_frontier'])}")
    print(f"Dominates live: {', '.join(report['dominates_live']) or '-'}")
    for name, item in sorted(
            results.items(), key=lambda pair: pair[1]["summary"]["mean_return_pct"],
            reverse=True)[:5]:
        summary = item["summary"]
        relative = item["relative_to_live"]
        print(
            f"{name}: mean={summary['mean_return_pct']:+.3f}% "
            f"worst={summary['worst_return_pct']:+.3f}% "
            f"worstDD={summary['worst_max_drawdown_pct']:.3f}% "
            f"W/T/L={relative['wins_vs_live']}/{relative['ties_vs_live']}/"
            f"{relative['losses_vs_live']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

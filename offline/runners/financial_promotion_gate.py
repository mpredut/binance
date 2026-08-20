#!/usr/bin/env python3
"""Compară două rapoarte financiare și aplică gate-ul conservator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from offline.backtests.promotion import evaluate_promotion  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.baseline.expanduser().resolve().open(encoding="utf-8") as handle:
        baseline = json.load(handle)
    with args.candidate.expanduser().resolve().open(encoding="utf-8") as handle:
        candidate = json.load(handle)
    result = evaluate_promotion(baseline, candidate)
    print("PROMOTE" if result["promote"] else "DO NOT PROMOTE")
    for name, scenario in result["scenarios"].items():
        deltas = scenario["wins_ties_losses"]
        print(
            f"{name}: {'PASS' if scenario['passed'] else 'FAIL'} "
            f"meanΔ={scenario['mean_return_delta_pp']:+.3f}pp "
            f"worstΔ={scenario['worst_return_delta_pp']:+.3f}pp "
            f"ddΔ={scenario['worst_drawdown_delta_pp']:+.3f}pp "
            f"W/T/L={deltas['wins']}/{deltas['ties']}/{deltas['losses']}"
        )
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
    return 0 if result["promote"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


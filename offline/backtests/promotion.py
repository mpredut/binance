"""Gate conservator de promovare pentru două benchmarkuri financiare."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class PromotionThresholds:
    min_mean_improvement_pp: float = 0.10
    max_worst_return_degradation_pp: float = 0.25
    max_drawdown_increase_pp: float = 0.25
    min_windows: int = 20
    min_active_windows: int = 10
    max_one_sided_sign_pvalue: float = 0.10
    require_more_wins_than_losses: bool = True


def _identity(report: dict) -> tuple:
    dataset = report.get("dataset", {})
    walk = report.get("walk_forward", {})
    return (
        dataset.get("sha256"), dataset.get("bars"),
        walk.get("train"), walk.get("validation"), walk.get("test"),
        walk.get("step"), walk.get("warmup"),
    )


def _one_sided_sign_pvalue(wins: int, losses: int) -> float:
    """P(X >= wins), X~Binomial(wins+losses, 0.5), ignorând egalitățile."""
    active = wins + losses
    if active == 0:
        return 1.0
    return sum(math.comb(active, value) for value in range(wins, active + 1)) / (
        2 ** active
    )


def evaluate_promotion(
    baseline: dict,
    candidate: dict,
    thresholds: PromotionThresholds | None = None,
) -> dict:
    """Candidatul trece numai dacă este robust în TOATE scenariile comune."""
    limits = thresholds or PromotionThresholds()
    if _identity(baseline) != _identity(candidate):
        raise ValueError("baseline și candidat folosesc dataset/ferestre diferite")

    base_scenarios = baseline.get("scenarios") or {}
    candidate_scenarios = candidate.get("scenarios") or {}
    if not base_scenarios or set(base_scenarios) != set(candidate_scenarios):
        raise ValueError("baseline și candidat trebuie să aibă aceleași scenarii")

    scenario_results = {}
    for name in sorted(base_scenarios):
        base = base_scenarios[name]
        cand = candidate_scenarios[name]
        base_windows = {window["key"]: window for window in base.get("windows", [])}
        cand_windows = {window["key"]: window for window in cand.get("windows", [])}
        if set(base_windows) != set(cand_windows):
            raise ValueError(f"ferestre diferite în scenariul {name}")
        deltas = [
            float(cand_windows[key]["return_pct"])
            - float(base_windows[key]["return_pct"])
            for key in sorted(base_windows)
        ]
        tolerance = 1e-10
        wins = sum(delta > tolerance for delta in deltas)
        ties = sum(abs(delta) <= tolerance for delta in deltas)
        losses = sum(delta < -tolerance for delta in deltas)
        active_windows = wins + losses
        sign_pvalue = _one_sided_sign_pvalue(wins, losses)
        base_aggregate = base["aggregate"]
        candidate_aggregate = cand["aggregate"]
        mean_delta = (
            float(candidate_aggregate["mean_return_pct"])
            - float(base_aggregate["mean_return_pct"])
        )
        worst_delta = (
            float(candidate_aggregate["worst_return_pct"])
            - float(base_aggregate["worst_return_pct"])
        )
        drawdown_delta = (
            float(candidate_aggregate["worst_max_drawdown_pct"])
            - float(base_aggregate["worst_max_drawdown_pct"])
        )
        checks = {
            "enough_windows": len(deltas) >= limits.min_windows,
            "enough_active_windows": active_windows >= limits.min_active_windows,
            "material_mean_improvement": mean_delta >= limits.min_mean_improvement_pp,
            "worst_return_preserved": (
                worst_delta >= -limits.max_worst_return_degradation_pp
            ),
            "drawdown_preserved": (
                drawdown_delta <= limits.max_drawdown_increase_pp
            ),
            "pairwise_robust": (
                wins > losses if limits.require_more_wins_than_losses else True
            ),
            "sign_test_support": (
                sign_pvalue <= limits.max_one_sided_sign_pvalue
            ),
        }
        scenario_results[name] = {
            "passed": all(checks.values()),
            "checks": checks,
            "mean_return_delta_pp": mean_delta,
            "worst_return_delta_pp": worst_delta,
            "worst_drawdown_delta_pp": drawdown_delta,
            "wins_ties_losses": {"wins": wins, "ties": ties, "losses": losses},
            "active_windows": active_windows,
            "active_window_rate_pct": (
                active_windows / len(deltas) * 100.0 if deltas else 0.0
            ),
            "one_sided_sign_pvalue": sign_pvalue,
        }

    return {
        "promote": all(item["passed"] for item in scenario_results.values()),
        "thresholds": asdict(limits),
        "scenarios": scenario_results,
    }

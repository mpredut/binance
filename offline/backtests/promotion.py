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


@dataclass(frozen=True)
class RiskAdjustedThresholds:
    """Praguri pentru un candidat defensiv, fără a-l confunda cu unul de return."""

    min_median_calmar_improvement_ratio: float = 0.15
    max_median_sortino_degradation_ratio: float = 0.05
    max_mean_return_degradation_pp: float = 0.15
    max_worst_return_degradation_pp: float = 0.25
    min_worst_drawdown_improvement_pp: float = 1.0
    min_worst_cvar_improvement_pp: float = 0.25
    max_mean_exposure_increase_pp: float = 0.0
    min_windows: int = 20
    min_active_windows: int = 10
    max_one_sided_drawdown_sign_pvalue: float = 0.10
    require_more_drawdown_wins_than_losses: bool = True


def _identity(report: dict) -> tuple:
    dataset = report.get("dataset", {})
    walk = report.get("walk_forward", {})
    return (
        dataset.get("sha256"), dataset.get("bars"),
        report.get("initial_capital_usd"), walk.get("interval_minutes"),
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


def _finite(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _relative_change(baseline, candidate) -> float | None:
    """Schimbare relativă numai pentru o bază pozitivă, comparabilă."""
    base = _finite(baseline)
    cand = _finite(candidate)
    if base is None or cand is None or base <= 0:
        return None
    return (cand - base) / base


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
        if base.get("assumptions") != cand.get("assumptions"):
            raise ValueError(f"ipoteze de execuție diferite în scenariul {name}")
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
        "objective": "RETURN",
        "thresholds": asdict(limits),
        "scenarios": scenario_results,
    }


def evaluate_risk_adjusted_promotion(
    baseline: dict,
    candidate: dict,
    thresholds: RiskAdjustedThresholds | None = None,
) -> dict:
    """Evaluează o pistă defensivă pe metricile reale ale fold-urilor.

    Nu folosim ``mean_return / worst_drawdown`` drept Calmar: numărătorul și
    numitorul ar putea proveni din fold-uri diferite. Benchmarkul calculează
    Calmar pe curba fiecărui fold, iar gate-ul compară medianele acelor valori.
    """
    limits = thresholds or RiskAdjustedThresholds()
    if _identity(baseline) != _identity(candidate):
        raise ValueError("baseline și candidat folosesc dataset/ferestre diferite")

    base_scenarios = baseline.get("scenarios") or {}
    candidate_scenarios = candidate.get("scenarios") or {}
    if not base_scenarios or set(base_scenarios) != set(candidate_scenarios):
        raise ValueError("baseline și candidat trebuie să aibă aceleași scenarii")

    scenario_results = {}
    tolerance = 1e-10
    for name in sorted(base_scenarios):
        base = base_scenarios[name]
        cand = candidate_scenarios[name]
        if base.get("assumptions") != cand.get("assumptions"):
            raise ValueError(f"ipoteze de execuție diferite în scenariul {name}")
        base_windows = {window["key"]: window for window in base.get("windows", [])}
        cand_windows = {window["key"]: window for window in cand.get("windows", [])}
        if set(base_windows) != set(cand_windows):
            raise ValueError(f"ferestre diferite în scenariul {name}")

        drawdown_improvements = [
            float(base_windows[key]["max_drawdown_pct"])
            - float(cand_windows[key]["max_drawdown_pct"])
            for key in sorted(base_windows)
        ]
        wins = sum(delta > tolerance for delta in drawdown_improvements)
        ties = sum(abs(delta) <= tolerance for delta in drawdown_improvements)
        losses = sum(delta < -tolerance for delta in drawdown_improvements)
        active_windows = wins + losses
        sign_pvalue = _one_sided_sign_pvalue(wins, losses)

        base_aggregate = base["aggregate"]
        candidate_aggregate = cand["aggregate"]
        mean_delta = (
            float(candidate_aggregate["mean_return_pct"])
            - float(base_aggregate["mean_return_pct"])
        )
        worst_return_delta = (
            float(candidate_aggregate["worst_return_pct"])
            - float(base_aggregate["worst_return_pct"])
        )
        worst_drawdown_improvement = (
            float(base_aggregate["worst_max_drawdown_pct"])
            - float(candidate_aggregate["worst_max_drawdown_pct"])
        )
        base_cvar = _finite(base_aggregate.get("worst_cvar_95_pct"))
        candidate_cvar = _finite(candidate_aggregate.get("worst_cvar_95_pct"))
        cvar_improvement = (
            candidate_cvar - base_cvar
            if base_cvar is not None and candidate_cvar is not None else None
        )
        base_exposure = _finite(base_aggregate.get("mean_exposure_pct"))
        candidate_exposure = _finite(candidate_aggregate.get("mean_exposure_pct"))
        exposure_delta = (
            candidate_exposure - base_exposure
            if base_exposure is not None and candidate_exposure is not None else None
        )
        calmar_improvement = _relative_change(
            base_aggregate.get("median_calmar"),
            candidate_aggregate.get("median_calmar"),
        )
        sortino_change = _relative_change(
            base_aggregate.get("median_sortino"),
            candidate_aggregate.get("median_sortino"),
        )

        checks = {
            "enough_windows": len(drawdown_improvements) >= limits.min_windows,
            "enough_active_windows": active_windows >= limits.min_active_windows,
            "positive_return": float(candidate_aggregate["mean_return_pct"]) > 0,
            "return_noninferior": mean_delta >= -limits.max_mean_return_degradation_pp,
            "worst_return_preserved": (
                worst_return_delta >= -limits.max_worst_return_degradation_pp
            ),
            "calmar_available": calmar_improvement is not None,
            "material_calmar_improvement": (
                calmar_improvement is not None
                and calmar_improvement >= limits.min_median_calmar_improvement_ratio
            ),
            "sortino_preserved": (
                sortino_change is not None
                and sortino_change >= -limits.max_median_sortino_degradation_ratio
            ),
            "material_drawdown_improvement": (
                worst_drawdown_improvement
                >= limits.min_worst_drawdown_improvement_pp
            ),
            "material_cvar_improvement": (
                cvar_improvement is not None
                and cvar_improvement >= limits.min_worst_cvar_improvement_pp
            ),
            "exposure_not_increased": (
                exposure_delta is not None
                and exposure_delta <= limits.max_mean_exposure_increase_pp + tolerance
            ),
            "drawdown_pairwise_robust": (
                wins > losses
                if limits.require_more_drawdown_wins_than_losses else True
            ),
            "drawdown_sign_test_support": (
                sign_pvalue <= limits.max_one_sided_drawdown_sign_pvalue
            ),
        }
        scenario_results[name] = {
            "passed": all(checks.values()),
            "checks": checks,
            "mean_return_delta_pp": mean_delta,
            "worst_return_delta_pp": worst_return_delta,
            "worst_drawdown_improvement_pp": worst_drawdown_improvement,
            "worst_cvar_improvement_pp": cvar_improvement,
            "mean_exposure_delta_pp": exposure_delta,
            "median_calmar_improvement_ratio": calmar_improvement,
            "median_sortino_change_ratio": sortino_change,
            "drawdown_wins_ties_losses": {
                "wins": wins, "ties": ties, "losses": losses,
            },
            "active_windows": active_windows,
            "active_window_rate_pct": (
                active_windows / len(drawdown_improvements) * 100.0
                if drawdown_improvements else 0.0
            ),
            "one_sided_drawdown_sign_pvalue": sign_pvalue,
        }

    return {
        "promote": all(item["passed"] for item in scenario_results.values()),
        "objective": "DEFENSIVE",
        "thresholds": asdict(limits),
        "scenarios": scenario_results,
    }


def evaluate_dual_promotion(
    baseline: dict,
    candidate: dict,
    return_thresholds: PromotionThresholds | None = None,
    risk_thresholds: RiskAdjustedThresholds | None = None,
) -> dict:
    """Candidatul este eligibil prin RETURN sau DEFENSIVE, cu eticheta păstrată."""
    return_gate = evaluate_promotion(baseline, candidate, return_thresholds)
    defensive_gate = evaluate_risk_adjusted_promotion(
        baseline, candidate, risk_thresholds,
    )
    paths = []
    if return_gate["promote"]:
        paths.append("RETURN")
    if defensive_gate["promote"]:
        paths.append("DEFENSIVE")
    return {
        "promote": bool(paths),
        "promotion_paths": paths,
        "return_gate": return_gate,
        "defensive_gate": defensive_gate,
    }

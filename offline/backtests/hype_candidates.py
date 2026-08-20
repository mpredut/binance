"""Candidați HYPE preînregistrați, partajați de runnerele offline.

Lista este intenționat mică și fixă. Nu este un grid de optimizare și ordinea ei
nu reprezintă un clasament. Fiecare schimbare rămâne implicit oprită în live.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    overrides: dict


def _tp4(name: str) -> Candidate:
    return Candidate(name, "prag TP 4%", {"takeprofit_pct": 4.0})


def _dca15(name: str) -> Candidate:
    return Candidate(name, "DCA la scădere 1,5%", {"dca_drop_pct": 1.5})


def _dca_vol_m1(name: str) -> Candidate:
    return Candidate(
        name,
        "mărime DCA scalată cu volatilitatea: k=-1, referință 2%",
        {
            "dca_vol_scale_k": -1.0,
            "dca_vol_ref": 2.0,
            "dca_vol_interval": 240,
        },
    )


def _adaptive_trail(name: str) -> Candidate:
    return Candidate(
        name, "trailing adaptiv k=2, clamp 1,5-8%",
        {
            "tp_trail_adaptive": True,
            "tp_trail_k": 2.0,
            "tp_trail_min": 1.5,
            "tp_trail_max": 8.0,
            "tp_trail_vol_interval": 240,
        },
    )


def _dca_brake(name: str) -> Candidate:
    return Candidate(
        name, "blochează DCA în downtrend confirmat",
        {
            "dca_trend_brake": True,
            "dca_brake_min_pct": 1.5,
            "trend_interval": 240,
        },
    )


def _overlay650t8() -> Candidate:
    return Candidate(
        "overlay650t8", "overlay redus: top-up 650, trail 8%",
        {
            "trend_overlay": True,
            "trend_topup": 650.0,
            "trend_trail_pct": 8.0,
            "trend_interval": 240,
        },
    )


def hype_240_candidates() -> list[Candidate]:
    """Setul istoric al comparatorului walk-forward; păstrează numele rapoartelor."""
    return [
        Candidate("live", "configurația live neschimbată", {}),
        Candidate(
            "overlay_orig", "overlay original: top-up 2000, trail 5%",
            {"trend_overlay": True, "trend_topup": 2000.0,
             "trend_trail_pct": 5.0, "trend_interval": 240},
        ),
        _overlay650t8(),
        _adaptive_trail("A_adaptive_trail"),
        _dca_brake("B_dca_brake"),
        _tp4("tp_4"),
        _dca15("dca_drop_1_5"),
    ]


def financial_priority_candidates() -> list[Candidate]:
    """Candidații care trec prin scenariile central/stress și promotion gate."""
    return [
        Candidate("live", "configurația live neschimbată", {}),
        _tp4("tp4"),
        _dca15("dca15"),
        Candidate(
            "dca_progressive025",
            "DCA 1,25% inițial, apoi +0,25pp după fiecare DCA executat",
            {"dca_spacing_growth_pct": 0.25},
        ),
        _dca_vol_m1("dca_vol_m1"),
        _adaptive_trail("A_trail"),
        _dca_brake("B_dcabrake"),
        _overlay650t8(),
        Candidate(
            "trail_profit_floor_sl18",
            "trailing soft la minimum +1% brut; hard stop MARKET la -18%",
            {"tp_trail_profit_floor_pct": 1.0, "stop_loss_pct": 18.0},
        ),
    ]

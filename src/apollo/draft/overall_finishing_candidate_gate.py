from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

OVERALL_SHOOTING_SIGNAL = "overall_shooting_pct"
OVERALL_SHOOTING_STRENGTH = 0.05
OVERALL_SHOOTING_CANDIDATE_VERSION = "apollo-skater-v0.7-candidate-overall-shpct-shrink5"
ROBUSTNESS_COHORTS = (
    ("GP20 ALL", 20, None),
    ("GP30 ALL", 30, None),
    ("GP40 ALL", 40, None),
    ("GP20 F", 20, "F"),
    ("GP20 D", 20, "D"),
)


@dataclass(frozen=True, slots=True)
class OverallFinishingGateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class OverallFinishingGateCohort:
    label: str
    min_actual_games: int
    position_group: str | None
    player_seasons: int
    applied: int
    metrics: tuple[OverallFinishingGateMetric, ...]
    goals_improved_years: int
    worst_goals_gain: float
    points_improved_years: int
    worst_points_gain: float
    baseline_top25: float
    candidate_top25: float


@dataclass(frozen=True, slots=True)
class OverallFinishingGateResult:
    latest_target_season: int
    target_seasons: tuple[int, ...]
    cohorts: tuple[OverallFinishingGateCohort, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _top25(result: ProjectionBacktestResult) -> float:
    return next(
        overlap.overlap_rate
        for overlap in result.top_k_points
        if overlap.requested_k == 25
    )


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("Overall finishing candidate gate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(float(value), weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted(usable)


def build_overall_finishing_gate_cohort(
    *,
    label: str,
    min_actual_games: int,
    position_group: str | None,
    season_results: tuple[tuple[ProjectionBacktestResult, ProjectionBacktestResult, int], ...],
) -> OverallFinishingGateCohort:
    if not season_results:
        raise ProjectionError("Overall finishing gate cohort requires season results")

    stat_names = (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "powerPlayPoints",
        "shots",
        "hits",
        "blockedShots",
    )
    metrics: list[OverallFinishingGateMetric] = []
    for stat_name in stat_names:
        baseline_mae = _weighted(
            [(_metric(base, stat_name).mae, base.evaluated_players) for base, _, _ in season_results]
        )
        candidate_mae = _weighted(
            [
                (_metric(candidate, stat_name).mae, candidate.evaluated_players)
                for _, candidate, _ in season_results
            ]
        )
        baseline_rho = _weighted_optional(
            [
                (_metric(base, stat_name).spearman_rho, base.evaluated_players)
                for base, _, _ in season_results
            ]
        )
        candidate_rho = _weighted_optional(
            [
                (_metric(candidate, stat_name).spearman_rho, candidate.evaluated_players)
                for _, candidate, _ in season_results
            ]
        )
        metrics.append(
            OverallFinishingGateMetric(
                stat_name=stat_name,
                baseline_mae=baseline_mae,
                candidate_mae=candidate_mae,
                mae_gain=baseline_mae - candidate_mae,
                baseline_rho=baseline_rho,
                candidate_rho=candidate_rho,
            )
        )

    goals_gains = [
        _metric(base, "goals").mae - _metric(candidate, "goals").mae
        for base, candidate, _ in season_results
    ]
    points_gains = [
        _metric(base, "points").mae - _metric(candidate, "points").mae
        for base, candidate, _ in season_results
    ]
    return OverallFinishingGateCohort(
        label=label,
        min_actual_games=min_actual_games,
        position_group=position_group,
        player_seasons=sum(base.evaluated_players for base, _, _ in season_results),
        applied=sum(applied for _, _, applied in season_results),
        metrics=tuple(metrics),
        goals_improved_years=sum(gain > 0 for gain in goals_gains),
        worst_goals_gain=min(goals_gains),
        points_improved_years=sum(gain > 0 for gain in points_gains),
        worst_points_gain=min(points_gains),
        baseline_top25=sum(_top25(base) for base, _, _ in season_results) / len(season_results),
        candidate_top25=sum(_top25(candidate) for _, candidate, _ in season_results)
        / len(season_results),
    )


def build_overall_finishing_gate_result(
    *,
    latest_target_season: int,
    target_seasons: tuple[int, ...],
    cohorts: tuple[OverallFinishingGateCohort, ...],
) -> OverallFinishingGateResult:
    if not cohorts:
        raise ProjectionError("Overall finishing candidate gate requires cohorts")
    return OverallFinishingGateResult(
        latest_target_season=latest_target_season,
        target_seasons=target_seasons,
        cohorts=cohorts,
    )

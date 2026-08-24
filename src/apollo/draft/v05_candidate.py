from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

V05_CANDIDATE_MODEL_VERSION = "apollo-skater-v0.5-candidate-sh-offense10"
V05_CANDIDATE_SHOOTING_STRENGTH = 0.10


@dataclass(frozen=True, slots=True)
class V05CandidateSeasonResult:
    target_season: int
    baseline: ProjectionBacktestResult
    candidate: ProjectionBacktestResult
    shooting_context_applied: int


@dataclass(frozen=True, slots=True)
class V05CandidateAggregateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class V05CandidateAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    shooting_context_applied: int
    season_results: tuple[V05CandidateSeasonResult, ...]
    metrics: tuple[V05CandidateAggregateMetric, ...]
    points_improved_years: int
    worst_points_mae_gain: float
    baseline_top25_overlap_rate: float
    candidate_top25_overlap_rate: float


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        raise ProjectionError("v0.5 candidate aggregate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total_weight


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(float(value), weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted(usable)


def _top25(result: ProjectionBacktestResult) -> float:
    return next(
        overlap.overlap_rate
        for overlap in result.top_k_points
        if overlap.requested_k == 25
    )


def build_v05_candidate_aggregate_result(
    season_results: tuple[V05CandidateSeasonResult, ...],
) -> V05CandidateAggregateResult:
    if not season_results:
        raise ProjectionError("v0.5 candidate aggregate requires at least one season")

    metrics: list[V05CandidateAggregateMetric] = []
    for stat_name in ("points", "goals", "assists", "shots", "powerPlayPoints", "hits", "blockedShots"):
        baseline_mae = _weighted(
            [
                (_metric(result.baseline, stat_name).mae, result.baseline.evaluated_players)
                for result in season_results
            ]
        )
        candidate_mae = _weighted(
            [
                (_metric(result.candidate, stat_name).mae, result.candidate.evaluated_players)
                for result in season_results
            ]
        )
        baseline_rho = _weighted_optional(
            [
                (
                    _metric(result.baseline, stat_name).spearman_rho,
                    result.baseline.evaluated_players,
                )
                for result in season_results
            ]
        )
        candidate_rho = _weighted_optional(
            [
                (
                    _metric(result.candidate, stat_name).spearman_rho,
                    result.candidate.evaluated_players,
                )
                for result in season_results
            ]
        )
        metrics.append(
            V05CandidateAggregateMetric(
                stat_name=stat_name,
                baseline_mae=baseline_mae,
                candidate_mae=candidate_mae,
                mae_gain=baseline_mae - candidate_mae,
                baseline_rho=baseline_rho,
                candidate_rho=candidate_rho,
            )
        )

    points_gains = [
        _metric(result.baseline, "points").mae - _metric(result.candidate, "points").mae
        for result in season_results
    ]

    return V05CandidateAggregateResult(
        target_seasons=tuple(result.target_season for result in season_results),
        baseline_player_seasons=sum(
            result.baseline.evaluated_players for result in season_results
        ),
        shooting_context_applied=sum(
            result.shooting_context_applied for result in season_results
        ),
        season_results=season_results,
        metrics=tuple(metrics),
        points_improved_years=sum(gain > 0 for gain in points_gains),
        worst_points_mae_gain=min(points_gains),
        baseline_top25_overlap_rate=sum(_top25(result.baseline) for result in season_results)
        / len(season_results),
        candidate_top25_overlap_rate=sum(_top25(result.candidate) for result in season_results)
        / len(season_results),
    )

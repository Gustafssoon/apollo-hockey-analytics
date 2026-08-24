from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

ASSIST_RATE_CANDIDATE_STRENGTHS = (0.10, 0.20)
ASSIST_RATE_CANDIDATE_MODEL_VERSIONS = {
    0.10: "apollo-skater-v0.6-candidate-a60-10",
    0.20: "apollo-skater-v0.6-candidate-a60-20",
}


@dataclass(frozen=True, slots=True)
class AssistRateCandidateVariantSeasonResult:
    strength: float
    model_version: str
    result: ProjectionBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class AssistRateCandidateSeasonResult:
    target_season: int
    baseline: ProjectionBacktestResult
    variants: tuple[AssistRateCandidateVariantSeasonResult, ...]


@dataclass(frozen=True, slots=True)
class AssistRateCandidateAggregateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class AssistRateCandidateAggregateVariant:
    strength: float
    model_version: str
    applied: int
    metrics: tuple[AssistRateCandidateAggregateMetric, ...]
    points_improved_years: int
    worst_points_mae_gain: float
    assists_improved_years: int
    worst_assists_mae_gain: float
    baseline_top25_overlap_rate: float
    candidate_top25_overlap_rate: float


@dataclass(frozen=True, slots=True)
class AssistRateCandidateAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    season_results: tuple[AssistRateCandidateSeasonResult, ...]
    variants: tuple[AssistRateCandidateAggregateVariant, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("Assist-rate candidate aggregate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total


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


def _variant_for_strength(
    result: AssistRateCandidateSeasonResult,
    strength: float,
) -> AssistRateCandidateVariantSeasonResult:
    return next(variant for variant in result.variants if variant.strength == strength)


def build_assist_rate_candidate_aggregate_result(
    season_results: tuple[AssistRateCandidateSeasonResult, ...],
) -> AssistRateCandidateAggregateResult:
    if not season_results:
        raise ProjectionError("Assist-rate candidate aggregate requires at least one season")

    aggregate_variants: list[AssistRateCandidateAggregateVariant] = []
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
    for strength in ASSIST_RATE_CANDIDATE_STRENGTHS:
        season_variants = [_variant_for_strength(result, strength) for result in season_results]
        metrics: list[AssistRateCandidateAggregateMetric] = []
        for stat_name in stat_names:
            baseline_mae = _weighted(
                [
                    (_metric(result.baseline, stat_name).mae, result.baseline.evaluated_players)
                    for result in season_results
                ]
            )
            candidate_mae = _weighted(
                [
                    (_metric(variant.result, stat_name).mae, variant.result.evaluated_players)
                    for variant in season_variants
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
                        _metric(variant.result, stat_name).spearman_rho,
                        variant.result.evaluated_players,
                    )
                    for variant in season_variants
                ]
            )
            metrics.append(
                AssistRateCandidateAggregateMetric(
                    stat_name=stat_name,
                    baseline_mae=baseline_mae,
                    candidate_mae=candidate_mae,
                    mae_gain=baseline_mae - candidate_mae,
                    baseline_rho=baseline_rho,
                    candidate_rho=candidate_rho,
                )
            )

        points_gains = [
            _metric(result.baseline, "points").mae - _metric(variant.result, "points").mae
            for result, variant in zip(season_results, season_variants, strict=True)
        ]
        assists_gains = [
            _metric(result.baseline, "assists").mae - _metric(variant.result, "assists").mae
            for result, variant in zip(season_results, season_variants, strict=True)
        ]
        aggregate_variants.append(
            AssistRateCandidateAggregateVariant(
                strength=strength,
                model_version=season_variants[0].model_version,
                applied=sum(variant.applied for variant in season_variants),
                metrics=tuple(metrics),
                points_improved_years=sum(gain > 0 for gain in points_gains),
                worst_points_mae_gain=min(points_gains),
                assists_improved_years=sum(gain > 0 for gain in assists_gains),
                worst_assists_mae_gain=min(assists_gains),
                baseline_top25_overlap_rate=sum(
                    _top25(result.baseline) for result in season_results
                )
                / len(season_results),
                candidate_top25_overlap_rate=sum(
                    _top25(variant.result) for variant in season_variants
                )
                / len(season_variants),
            )
        )

    return AssistRateCandidateAggregateResult(
        target_seasons=tuple(result.target_season for result in season_results),
        baseline_player_seasons=sum(
            result.baseline.evaluated_players for result in season_results
        ),
        season_results=season_results,
        variants=tuple(aggregate_variants),
    )

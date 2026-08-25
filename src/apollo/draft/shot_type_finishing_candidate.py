from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

SHOT_TYPE_FINISHING_SIGNALS = (
    "overall_shooting_pct",
    "tip_deflect_shooting_pct",
    "wrist_shooting_pct",
    "snap_shooting_pct",
)
SHOT_TYPE_FINISHING_STRENGTHS = (0.05, 0.10, 0.20)


def candidate_model_version(signal_name: str, strength: float) -> str:
    if signal_name not in SHOT_TYPE_FINISHING_SIGNALS:
        raise ProjectionError(f"Unknown shot-type finishing signal: {signal_name}")
    if strength not in SHOT_TYPE_FINISHING_STRENGTHS:
        raise ProjectionError(f"Unsupported shot-type finishing strength: {strength}")
    pct = int(strength * 100)
    short = {
        "overall_shooting_pct": "overall-shpct",
        "tip_deflect_shooting_pct": "tip-deflect-shpct",
        "wrist_shooting_pct": "wrist-shpct",
        "snap_shooting_pct": "snap-shpct",
    }[signal_name]
    return f"apollo-skater-v0.7-candidate-{short}-shrink{pct}"


@dataclass(frozen=True, slots=True)
class ShotTypeFinishingVariantSeasonResult:
    signal_name: str
    strength: float
    model_version: str
    result: ProjectionBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class ShotTypeFinishingSeasonResult:
    target_season: int
    baseline: ProjectionBacktestResult
    variants: tuple[ShotTypeFinishingVariantSeasonResult, ...]


@dataclass(frozen=True, slots=True)
class ShotTypeFinishingAggregateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class ShotTypeFinishingAggregateVariant:
    signal_name: str
    strength: float
    model_version: str
    applied: int
    metrics: tuple[ShotTypeFinishingAggregateMetric, ...]
    goals_improved_years: int
    worst_goals_mae_gain: float
    points_improved_years: int
    worst_points_mae_gain: float
    baseline_top25_overlap_rate: float
    candidate_top25_overlap_rate: float


@dataclass(frozen=True, slots=True)
class ShotTypeFinishingAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    season_results: tuple[ShotTypeFinishingSeasonResult, ...]
    variants: tuple[ShotTypeFinishingAggregateVariant, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("Shot-type finishing aggregate requires evaluated skaters")
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


def _variant_for(
    result: ShotTypeFinishingSeasonResult,
    signal_name: str,
    strength: float,
) -> ShotTypeFinishingVariantSeasonResult:
    return next(
        variant
        for variant in result.variants
        if variant.signal_name == signal_name and variant.strength == strength
    )


def build_shot_type_finishing_aggregate_result(
    season_results: tuple[ShotTypeFinishingSeasonResult, ...],
) -> ShotTypeFinishingAggregateResult:
    if not season_results:
        raise ProjectionError("Shot-type finishing aggregate requires at least one season")

    aggregate_variants: list[ShotTypeFinishingAggregateVariant] = []
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
    for signal_name in SHOT_TYPE_FINISHING_SIGNALS:
        for strength in SHOT_TYPE_FINISHING_STRENGTHS:
            season_variants = [
                _variant_for(result, signal_name, strength) for result in season_results
            ]
            metrics: list[ShotTypeFinishingAggregateMetric] = []
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
                    ShotTypeFinishingAggregateMetric(
                        stat_name=stat_name,
                        baseline_mae=baseline_mae,
                        candidate_mae=candidate_mae,
                        mae_gain=baseline_mae - candidate_mae,
                        baseline_rho=baseline_rho,
                        candidate_rho=candidate_rho,
                    )
                )

            goals_gains = [
                _metric(result.baseline, "goals").mae - _metric(variant.result, "goals").mae
                for result, variant in zip(season_results, season_variants, strict=True)
            ]
            points_gains = [
                _metric(result.baseline, "points").mae - _metric(variant.result, "points").mae
                for result, variant in zip(season_results, season_variants, strict=True)
            ]
            aggregate_variants.append(
                ShotTypeFinishingAggregateVariant(
                    signal_name=signal_name,
                    strength=strength,
                    model_version=season_variants[0].model_version,
                    applied=sum(variant.applied for variant in season_variants),
                    metrics=tuple(metrics),
                    goals_improved_years=sum(gain > 0 for gain in goals_gains),
                    worst_goals_mae_gain=min(goals_gains),
                    points_improved_years=sum(gain > 0 for gain in points_gains),
                    worst_points_mae_gain=min(points_gains),
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

    return ShotTypeFinishingAggregateResult(
        target_seasons=tuple(result.target_season for result in season_results),
        baseline_player_seasons=sum(
            result.baseline.evaluated_players for result in season_results
        ),
        season_results=season_results,
        variants=tuple(aggregate_variants),
    )

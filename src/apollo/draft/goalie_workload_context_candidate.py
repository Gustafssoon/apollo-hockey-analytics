from dataclasses import dataclass

from apollo.draft.goalie_baseline import (
    GoalieBacktestMetric,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
)
from apollo.draft.goalie_workload_candidate import apply_workload_to_baseline
from apollo.draft.projections import ProjectionError

LATEST_SHARE_STRENGTHS = (0.05, 0.10, 0.20)
AGE_SLOPES = (0.005, 0.010, 0.020)
MIN_WORKLOAD_FACTOR = 0.80
MAX_WORKLOAD_FACTOR = 1.20


@dataclass(frozen=True, slots=True)
class GoalieWorkloadContextVariantSpec:
    name: str
    signal: str
    parameter: float


GOALIE_WORKLOAD_CONTEXT_VARIANTS = (
    *(GoalieWorkloadContextVariantSpec(f"latest-share-{int(value * 100)}", "latest_share", value) for value in LATEST_SHARE_STRENGTHS),
    *(GoalieWorkloadContextVariantSpec(f"age-{value * 100:.1f}", "age", value) for value in AGE_SLOPES),
)


@dataclass(frozen=True, slots=True)
class GoalieWorkloadContextSeasonVariant:
    spec: GoalieWorkloadContextVariantSpec
    result: GoalieBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class GoalieWorkloadContextSeasonResult:
    target_season: int
    baseline: GoalieBacktestResult
    variants: tuple[GoalieWorkloadContextSeasonVariant, ...]


@dataclass(frozen=True, slots=True)
class GoalieWorkloadContextVariantAggregate:
    spec: GoalieWorkloadContextVariantSpec
    player_seasons: int
    applied: int
    metrics: tuple[GoalieBacktestMetric, ...]
    improved_years: int
    worst_gs_mae_gain: float


@dataclass(frozen=True, slots=True)
class GoalieWorkloadContextAggregate:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    variants: tuple[GoalieWorkloadContextVariantAggregate, ...]


def _clamp_factor(value: float) -> float:
    return min(MAX_WORKLOAD_FACTOR, max(MIN_WORKLOAD_FACTOR, value))


def latest_share_factor(latest_share: float, prior_share: float, strength: float) -> float:
    if latest_share < 0 or prior_share <= 0:
        raise ProjectionError("Goalie latest-share candidate requires non-negative share and positive prior")
    if strength < 0:
        raise ProjectionError("Goalie latest-share strength must be non-negative")
    ratio = latest_share / prior_share
    return _clamp_factor(1.0 - strength * (ratio - 1.0))


def age_factor(age: float, prior_age: float, slope: float) -> float:
    if age <= 0 or prior_age <= 0:
        raise ProjectionError("Goalie age candidate requires positive age and prior")
    if slope < 0:
        raise ProjectionError("Goalie age slope must be non-negative")
    return _clamp_factor(1.0 - slope * (age - prior_age))


def apply_context_factor(
    baseline: GoalieBacktestPlayer,
    factor: float,
) -> GoalieBacktestPlayer:
    projected_starts = min(82.0, max(0.0, baseline.projected_starts * factor))
    return apply_workload_to_baseline(baseline, projected_starts)


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def build_goalie_workload_context_aggregate(
    results: tuple[GoalieWorkloadContextSeasonResult, ...],
) -> GoalieWorkloadContextAggregate:
    if not results:
        raise ProjectionError("Goalie workload context aggregate requires season results")
    total_n = sum(item.baseline.evaluated_goalies for item in results)
    if total_n <= 0:
        raise ProjectionError("Goalie workload context aggregate requires evaluated goalies")

    aggregates: list[GoalieWorkloadContextVariantAggregate] = []
    stat_names = (
        "gamesStarted",
        "wins",
        "saves",
        "goalsAgainst",
        "shutouts",
        "savePctg",
        "goalsAgainstAvg",
    )
    for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS:
        season_variants = [
            next(variant for variant in item.variants if variant.spec.name == spec.name)
            for item in results
        ]
        metrics: list[GoalieBacktestMetric] = []
        for stat_name in stat_names:
            pairs = [
                (_metric(variant.result, stat_name), item.baseline.evaluated_goalies)
                for variant, item in zip(season_variants, results, strict=True)
            ]
            mae = sum(metric.mae * n for metric, n in pairs) / total_n
            rho_pairs = [
                (metric.spearman_rho, n)
                for metric, n in pairs
                if metric.spearman_rho is not None
            ]
            rho = (
                None
                if not rho_pairs
                else sum(float(value) * n for value, n in rho_pairs)
                / sum(n for _, n in rho_pairs)
            )
            metrics.append(GoalieBacktestMetric(stat_name, mae, rho, None, None))

        gs_gains = [
            _metric(item.baseline, "gamesStarted").mae
            - _metric(variant.result, "gamesStarted").mae
            for item, variant in zip(results, season_variants, strict=True)
        ]
        aggregates.append(
            GoalieWorkloadContextVariantAggregate(
                spec=spec,
                player_seasons=total_n,
                applied=sum(variant.applied for variant in season_variants),
                metrics=tuple(metrics),
                improved_years=sum(gain > 0 for gain in gs_gains),
                worst_gs_mae_gain=min(gs_gains),
            )
        )

    return GoalieWorkloadContextAggregate(
        target_seasons=tuple(item.target_season for item in results),
        baseline_player_seasons=total_n,
        variants=tuple(aggregates),
    )

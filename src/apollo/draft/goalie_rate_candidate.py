from dataclasses import dataclass

from apollo.draft.goalie_baseline import (
    GoalieBacktestMetric,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
)
from apollo.draft.projections import ProjectionError

GOALIE_RATE_STRENGTHS = (0.05, 0.10, 0.20)


@dataclass(frozen=True, slots=True)
class GoalieRateVariantSpec:
    name: str
    stat_name: str
    strength: float


GOALIE_RATE_VARIANTS = (
    *(
        GoalieRateVariantSpec(f"sv-{int(strength * 100)}", "savePctg", strength)
        for strength in GOALIE_RATE_STRENGTHS
    ),
    *(
        GoalieRateVariantSpec(f"gaa-{int(strength * 100)}", "goalsAgainstAvg", strength)
        for strength in GOALIE_RATE_STRENGTHS
    ),
)


@dataclass(frozen=True, slots=True)
class GoalieRateSeasonVariant:
    spec: GoalieRateVariantSpec
    result: GoalieBacktestResult


@dataclass(frozen=True, slots=True)
class GoalieRateSeasonResult:
    target_season: int
    baseline: GoalieBacktestResult
    variants: tuple[GoalieRateSeasonVariant, ...]
    save_pct_prior: float
    gaa_prior: float


@dataclass(frozen=True, slots=True)
class GoalieRateVariantAggregate:
    spec: GoalieRateVariantSpec
    player_seasons: int
    metrics: tuple[GoalieBacktestMetric, ...]
    improved_years: int
    worst_mae_gain: float


@dataclass(frozen=True, slots=True)
class GoalieRateAggregate:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    variants: tuple[GoalieRateVariantAggregate, ...]


def regress_to_prior(value: float, prior: float, strength: float) -> float:
    if strength < 0 or strength > 1:
        raise ProjectionError("Goalie rate regression strength must be between 0 and 1")
    if value < 0 or prior < 0:
        raise ProjectionError("Goalie rate regression requires non-negative rates")
    return value + strength * (prior - value)


def apply_rate_regression(
    baseline: GoalieBacktestPlayer,
    spec: GoalieRateVariantSpec,
    prior: float,
) -> GoalieBacktestPlayer:
    stats = dict(baseline.projected_stats)
    stats[spec.stat_name] = regress_to_prior(stats[spec.stat_name], prior, spec.strength)
    return GoalieBacktestPlayer(
        player_id=baseline.player_id,
        player_name=baseline.player_name,
        projected_starts=baseline.projected_starts,
        actual_starts=baseline.actual_starts,
        projected_stats=stats,
        actual_stats=baseline.actual_stats,
    )


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def build_goalie_rate_aggregate(
    results: tuple[GoalieRateSeasonResult, ...],
) -> GoalieRateAggregate:
    if not results:
        raise ProjectionError("Goalie rate aggregate requires season results")
    total_n = sum(item.baseline.evaluated_goalies for item in results)
    if total_n <= 0:
        raise ProjectionError("Goalie rate aggregate requires evaluated goalies")

    stat_names = tuple(metric.stat_name for metric in results[0].baseline.metrics)
    aggregates: list[GoalieRateVariantAggregate] = []
    for spec in GOALIE_RATE_VARIANTS:
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

        target_stat = spec.stat_name
        gains = [
            _metric(item.baseline, target_stat).mae
            - _metric(variant.result, target_stat).mae
            for item, variant in zip(results, season_variants, strict=True)
        ]
        aggregates.append(
            GoalieRateVariantAggregate(
                spec=spec,
                player_seasons=total_n,
                metrics=tuple(metrics),
                improved_years=sum(gain > 0 for gain in gains),
                worst_mae_gain=min(gains),
            )
        )

    return GoalieRateAggregate(
        target_seasons=tuple(item.target_season for item in results),
        baseline_player_seasons=total_n,
        variants=tuple(aggregates),
    )

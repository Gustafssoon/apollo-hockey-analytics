from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

HIT_REGRESSION_PSEUDO_GAMES = (5.0, 10.0, 20.0)


def candidate_model_version(pseudo_games: float) -> str:
    if pseudo_games not in HIT_REGRESSION_PSEUDO_GAMES:
        raise ProjectionError(f"Unsupported HIT pseudo-games: {pseudo_games}")
    value = int(pseudo_games) if pseudo_games.is_integer() else pseudo_games
    return f"apollo-regression-hits-pg{value}-candidate-v0.1"


@dataclass(frozen=True, slots=True)
class HitRegressionVariantSeasonResult:
    pseudo_games: float
    model_version: str
    result: ProjectionBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class HitRegressionSeasonResult:
    target_season: int
    baseline: ProjectionBacktestResult
    variants: tuple[HitRegressionVariantSeasonResult, ...]


@dataclass(frozen=True, slots=True)
class HitRegressionAggregateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class HitRegressionAggregateVariant:
    pseudo_games: float
    model_version: str
    applied: int
    metrics: tuple[HitRegressionAggregateMetric, ...]
    hit_improved_years: int
    worst_hit_mae_gain: float


@dataclass(frozen=True, slots=True)
class HitRegressionAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    season_results: tuple[HitRegressionSeasonResult, ...]
    variants: tuple[HitRegressionAggregateVariant, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("HIT regression aggregate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(float(value), weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted(usable)


def _variant_for(
    result: HitRegressionSeasonResult,
    pseudo_games: float,
) -> HitRegressionVariantSeasonResult:
    return next(variant for variant in result.variants if variant.pseudo_games == pseudo_games)


def build_hit_regression_aggregate_result(
    season_results: tuple[HitRegressionSeasonResult, ...],
) -> HitRegressionAggregateResult:
    if not season_results:
        raise ProjectionError("HIT regression aggregate requires at least one season")

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
    aggregate_variants: list[HitRegressionAggregateVariant] = []
    for pseudo_games in HIT_REGRESSION_PSEUDO_GAMES:
        season_variants = [_variant_for(result, pseudo_games) for result in season_results]
        metrics: list[HitRegressionAggregateMetric] = []
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
                HitRegressionAggregateMetric(
                    stat_name=stat_name,
                    baseline_mae=baseline_mae,
                    candidate_mae=candidate_mae,
                    mae_gain=baseline_mae - candidate_mae,
                    baseline_rho=baseline_rho,
                    candidate_rho=candidate_rho,
                )
            )

        hit_gains = [
            _metric(result.baseline, "hits").mae - _metric(variant.result, "hits").mae
            for result, variant in zip(season_results, season_variants, strict=True)
        ]
        aggregate_variants.append(
            HitRegressionAggregateVariant(
                pseudo_games=pseudo_games,
                model_version=season_variants[0].model_version,
                applied=sum(variant.applied for variant in season_variants),
                metrics=tuple(metrics),
                hit_improved_years=sum(gain > 0 for gain in hit_gains),
                worst_hit_mae_gain=min(hit_gains),
            )
        )

    return HitRegressionAggregateResult(
        target_seasons=tuple(result.target_season for result in season_results),
        baseline_player_seasons=sum(result.baseline.evaluated_players for result in season_results),
        season_results=season_results,
        variants=tuple(aggregate_variants),
    )

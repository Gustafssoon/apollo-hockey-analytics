from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

PP_DEPLOYMENT_SIGNALS = (
    "pp_toi_ratio",
    "pp_toi_share_ratio",
)
PP_DEPLOYMENT_STRENGTHS = (0.05, 0.10, 0.20)


def candidate_model_version(signal_name: str, strength: float) -> str:
    if signal_name not in PP_DEPLOYMENT_SIGNALS:
        raise ProjectionError(f"Unknown PP deployment signal: {signal_name}")
    if strength not in PP_DEPLOYMENT_STRENGTHS:
        raise ProjectionError(f"Unsupported PP deployment strength: {strength}")
    pct = int(strength * 100)
    short = {
        "pp_toi_ratio": "pp-toi",
        "pp_toi_share_ratio": "pp-share",
    }[signal_name]
    return f"apollo-ppp-v0.1-candidate-{short}-shrink{pct}"


@dataclass(frozen=True, slots=True)
class PPDeploymentVariantSeasonResult:
    signal_name: str
    strength: float
    model_version: str
    result: ProjectionBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class PPDeploymentSeasonResult:
    target_season: int
    baseline: ProjectionBacktestResult
    variants: tuple[PPDeploymentVariantSeasonResult, ...]


@dataclass(frozen=True, slots=True)
class PPDeploymentAggregateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class PPDeploymentAggregateVariant:
    signal_name: str
    strength: float
    model_version: str
    applied: int
    metrics: tuple[PPDeploymentAggregateMetric, ...]
    ppp_improved_years: int
    worst_ppp_mae_gain: float


@dataclass(frozen=True, slots=True)
class PPDeploymentAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    season_results: tuple[PPDeploymentSeasonResult, ...]
    variants: tuple[PPDeploymentAggregateVariant, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("PP deployment aggregate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(float(value), weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted(usable)


def _variant_for(
    result: PPDeploymentSeasonResult,
    signal_name: str,
    strength: float,
) -> PPDeploymentVariantSeasonResult:
    return next(
        variant
        for variant in result.variants
        if variant.signal_name == signal_name and variant.strength == strength
    )


def build_pp_deployment_aggregate_result(
    season_results: tuple[PPDeploymentSeasonResult, ...],
) -> PPDeploymentAggregateResult:
    if not season_results:
        raise ProjectionError("PP deployment aggregate requires at least one season")

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
    aggregate_variants: list[PPDeploymentAggregateVariant] = []
    for signal_name in PP_DEPLOYMENT_SIGNALS:
        for strength in PP_DEPLOYMENT_STRENGTHS:
            season_variants = [
                _variant_for(result, signal_name, strength) for result in season_results
            ]
            metrics: list[PPDeploymentAggregateMetric] = []
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
                    PPDeploymentAggregateMetric(
                        stat_name=stat_name,
                        baseline_mae=baseline_mae,
                        candidate_mae=candidate_mae,
                        mae_gain=baseline_mae - candidate_mae,
                        baseline_rho=baseline_rho,
                        candidate_rho=candidate_rho,
                    )
                )

            ppp_gains = [
                _metric(result.baseline, "powerPlayPoints").mae
                - _metric(variant.result, "powerPlayPoints").mae
                for result, variant in zip(season_results, season_variants, strict=True)
            ]
            aggregate_variants.append(
                PPDeploymentAggregateVariant(
                    signal_name=signal_name,
                    strength=strength,
                    model_version=season_variants[0].model_version,
                    applied=sum(variant.applied for variant in season_variants),
                    metrics=tuple(metrics),
                    ppp_improved_years=sum(gain > 0 for gain in ppp_gains),
                    worst_ppp_mae_gain=min(ppp_gains),
                )
            )

    return PPDeploymentAggregateResult(
        target_seasons=tuple(result.target_season for result in season_results),
        baseline_player_seasons=sum(result.baseline.evaluated_players for result in season_results),
        season_results=season_results,
        variants=tuple(aggregate_variants),
    )

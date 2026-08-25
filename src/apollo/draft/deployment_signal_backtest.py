from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

DEPLOYMENT_SIGNAL_NAMES = (
    "total_toi_ratio",
    "toi5v5_ratio",
    "pp_toi_ratio",
    "pp_toi_share_ratio",
)
DEPLOYMENT_TARGET_STATS = (
    "powerPlayPoints",
    "points",
    "shots",
    "goals",
    "assists",
)


@dataclass(frozen=True, slots=True)
class DeploymentSignalPlayer:
    player_id: int
    player_name: str
    projected_stats: dict[str, float]
    actual_stats: dict[str, float]
    weighted_signals: dict[str, float]


@dataclass(frozen=True, slots=True)
class DeploymentSignalMetric:
    signal_name: str
    target_stat: str
    evaluated_players: int
    residual_rho: float | None
    quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class DeploymentSignalBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    metrics: tuple[DeploymentSignalMetric, ...]


@dataclass(frozen=True, slots=True)
class DeploymentSignalAggregateMetric:
    signal_name: str
    target_stat: str
    player_seasons: int
    weighted_residual_rho: float | None
    year_signs: str
    weighted_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class DeploymentSignalAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[DeploymentSignalAggregateMetric, ...]


def deployment_signal_value(signal_name: str, stats: dict[str, float]) -> float | None:
    if signal_name == "total_toi_ratio":
        value = stats.get("timeOnIcePerGame")
    elif signal_name == "toi5v5_ratio":
        value = stats.get("timeOnIcePerGame5v5")
    elif signal_name == "pp_toi_ratio":
        value = stats.get("powerPlayTimeOnIcePerGame")
    elif signal_name == "pp_toi_share_ratio":
        pp_toi = stats.get("powerPlayTimeOnIcePerGame")
        total_toi = stats.get("timeOnIcePerGame")
        if pp_toi is None or total_toi is None or total_toi <= 0:
            return None
        value = pp_toi / total_toi
    else:
        raise ProjectionError(f"Unknown deployment signal: {signal_name}")
    if value is None or value < 0:
        return None
    return value


def build_weighted_deployment_signals(
    history: tuple[dict[str, float], ...],
    priors: tuple[dict[str, float], ...],
    *,
    min_signal_seasons: int = 3,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> dict[str, float]:
    if len(history) != len(priors):
        raise ProjectionError("Deployment history and priors must have equal length")
    if len(history) > len(season_weights):
        raise ProjectionError("More deployment history seasons than configured weights")
    if min_signal_seasons < 1 or min_signal_seasons > len(season_weights):
        raise ProjectionError(
            f"min_signal_seasons must be between 1 and {len(season_weights)}"
        )

    weighted: dict[str, float] = {}
    for signal_name in DEPLOYMENT_SIGNAL_NAMES:
        values: list[tuple[float, float]] = []
        for index, (stats, season_priors) in enumerate(zip(history, priors, strict=True)):
            value = deployment_signal_value(signal_name, stats)
            prior = season_priors.get(signal_name)
            if value is None or prior is None or prior <= 0:
                continue
            values.append((value / prior, season_weights[index]))
        if len(values) < min_signal_seasons:
            continue
        weight_sum = sum(weight for _, weight in values)
        if weight_sum > 0:
            weighted[signal_name] = sum(value * weight for value, weight in values) / weight_sum
    return weighted


def _stat_value(stats: dict[str, float], stat_name: str) -> float:
    if stat_name == "points":
        return stats["goals"] + stats["assists"]
    return stats[stat_name]


def _quartile_delta(signal: list[float], residuals: list[float]) -> float | None:
    if len(signal) != len(residuals) or len(signal) < 4:
        return None
    ordered = sorted(zip(signal, residuals, strict=True), key=lambda item: item[0])
    quartile_size = max(1, len(ordered) // 4)
    bottom = ordered[:quartile_size]
    top = ordered[-quartile_size:]
    return (
        sum(residual for _, residual in top) / len(top)
        - sum(residual for _, residual in bottom) / len(bottom)
    )


def build_deployment_signal_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[DeploymentSignalPlayer, ...],
) -> DeploymentSignalBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Deployment signal screen requires baseline-eligible skaters")

    metrics: list[DeploymentSignalMetric] = []
    for signal_name in DEPLOYMENT_SIGNAL_NAMES:
        eligible = [player for player in players if signal_name in player.weighted_signals]
        signals = [player.weighted_signals[signal_name] for player in eligible]
        for target_stat in DEPLOYMENT_TARGET_STATS:
            residuals = [
                _stat_value(player.actual_stats, target_stat)
                - _stat_value(player.projected_stats, target_stat)
                for player in eligible
            ]
            metrics.append(
                DeploymentSignalMetric(
                    signal_name=signal_name,
                    target_stat=target_stat,
                    evaluated_players=len(eligible),
                    residual_rho=spearman_rank_correlation(signals, residuals),
                    quartile_delta=_quartile_delta(signals, residuals),
                )
            )
    return DeploymentSignalBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        metrics=tuple(metrics),
    )


def _weighted_average(values: list[tuple[float, int]]) -> float | None:
    if not values:
        return None
    total = sum(weight for _, weight in values)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in values) / total


def _year_signs(metrics: list[DeploymentSignalMetric]) -> str:
    signs: list[str] = []
    for metric in metrics:
        value = metric.residual_rho
        if value is None or abs(value) < 0.02:
            signs.append("0")
        elif value > 0:
            signs.append("+")
        else:
            signs.append("-")
    return "".join(signs)


def build_deployment_signal_aggregate_result(
    results: tuple[DeploymentSignalBacktestResult, ...],
) -> DeploymentSignalAggregateResult:
    if not results:
        raise ProjectionError("Deployment signal aggregate requires at least one season")

    aggregate: list[DeploymentSignalAggregateMetric] = []
    for signal_name in DEPLOYMENT_SIGNAL_NAMES:
        for target_stat in DEPLOYMENT_TARGET_STATS:
            season_metrics = [
                next(
                    metric
                    for metric in result.metrics
                    if metric.signal_name == signal_name and metric.target_stat == target_stat
                )
                for result in results
            ]
            rho_values = [
                (float(metric.residual_rho), metric.evaluated_players)
                for metric in season_metrics
                if metric.residual_rho is not None and metric.evaluated_players > 0
            ]
            quartile_values = [
                (float(metric.quartile_delta), metric.evaluated_players)
                for metric in season_metrics
                if metric.quartile_delta is not None and metric.evaluated_players > 0
            ]
            aggregate.append(
                DeploymentSignalAggregateMetric(
                    signal_name=signal_name,
                    target_stat=target_stat,
                    player_seasons=sum(metric.evaluated_players for metric in season_metrics),
                    weighted_residual_rho=_weighted_average(rho_values),
                    year_signs=_year_signs(season_metrics),
                    weighted_quartile_delta=_weighted_average(quartile_values),
                )
            )
    return DeploymentSignalAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        metrics=tuple(aggregate),
    )

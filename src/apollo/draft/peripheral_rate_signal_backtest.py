from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

PERIPHERAL_RATE_SIGNALS = (
    "sog_pg_ratio",
    "hit_pg_ratio",
    "blk_pg_ratio",
)
PERIPHERAL_RATE_TARGETS = {
    "sog_pg_ratio": "shots",
    "hit_pg_ratio": "hits",
    "blk_pg_ratio": "blockedShots",
}


@dataclass(frozen=True, slots=True)
class PeripheralRateSignalPlayer:
    player_id: int
    player_name: str
    projected_stats: dict[str, float]
    actual_stats: dict[str, float]
    weighted_signals: dict[str, float]


@dataclass(frozen=True, slots=True)
class PeripheralRateSignalMetric:
    signal_name: str
    target_stat: str
    evaluated_players: int
    residual_rho: float | None
    quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class PeripheralRateSignalBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    metrics: tuple[PeripheralRateSignalMetric, ...]


@dataclass(frozen=True, slots=True)
class PeripheralRateSignalAggregateMetric:
    signal_name: str
    target_stat: str
    player_seasons: int
    weighted_residual_rho: float | None
    year_signs: str
    weighted_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class PeripheralRateSignalAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[PeripheralRateSignalAggregateMetric, ...]


def _signal_stat(signal_name: str) -> str:
    try:
        return PERIPHERAL_RATE_TARGETS[signal_name]
    except KeyError as exc:
        raise ProjectionError(f"Unknown peripheral rate signal: {signal_name}") from exc


def peripheral_rate_value(signal_name: str, stats: dict[str, float]) -> float | None:
    stat_name = _signal_stat(signal_name)
    games_played = stats.get("gamesPlayed")
    value = stats.get(stat_name)
    if games_played is None or games_played <= 0 or value is None or value < 0:
        return None
    return value / games_played


def build_weighted_peripheral_rate_signals(
    history: tuple[dict[str, float], ...],
    priors: tuple[dict[str, float], ...],
    *,
    min_signal_seasons: int = 3,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> dict[str, float]:
    if len(history) != len(priors):
        raise ProjectionError("Peripheral rate history and priors must have equal length")
    if len(history) > len(season_weights):
        raise ProjectionError("More peripheral rate history seasons than configured weights")
    if min_signal_seasons < 1 or min_signal_seasons > len(season_weights):
        raise ProjectionError(
            f"min_signal_seasons must be between 1 and {len(season_weights)}"
        )

    weighted: dict[str, float] = {}
    for signal_name in PERIPHERAL_RATE_SIGNALS:
        values: list[tuple[float, float]] = []
        for index, (stats, season_priors) in enumerate(zip(history, priors, strict=True)):
            value = peripheral_rate_value(signal_name, stats)
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


def build_peripheral_rate_signal_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[PeripheralRateSignalPlayer, ...],
) -> PeripheralRateSignalBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Peripheral rate signal screen requires baseline-eligible skaters")

    metrics: list[PeripheralRateSignalMetric] = []
    for signal_name in PERIPHERAL_RATE_SIGNALS:
        target_stat = PERIPHERAL_RATE_TARGETS[signal_name]
        eligible = [player for player in players if signal_name in player.weighted_signals]
        signals = [player.weighted_signals[signal_name] for player in eligible]
        residuals = [
            player.actual_stats[target_stat] - player.projected_stats[target_stat]
            for player in eligible
        ]
        metrics.append(
            PeripheralRateSignalMetric(
                signal_name=signal_name,
                target_stat=target_stat,
                evaluated_players=len(eligible),
                residual_rho=spearman_rank_correlation(signals, residuals),
                quartile_delta=_quartile_delta(signals, residuals),
            )
        )
    return PeripheralRateSignalBacktestResult(
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


def _year_signs(metrics: list[PeripheralRateSignalMetric]) -> str:
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


def build_peripheral_rate_signal_aggregate_result(
    results: tuple[PeripheralRateSignalBacktestResult, ...],
) -> PeripheralRateSignalAggregateResult:
    if not results:
        raise ProjectionError("Peripheral rate signal aggregate requires at least one season")

    aggregate: list[PeripheralRateSignalAggregateMetric] = []
    for signal_name in PERIPHERAL_RATE_SIGNALS:
        target_stat = PERIPHERAL_RATE_TARGETS[signal_name]
        season_metrics = [
            next(metric for metric in result.metrics if metric.signal_name == signal_name)
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
            PeripheralRateSignalAggregateMetric(
                signal_name=signal_name,
                target_stat=target_stat,
                player_seasons=sum(metric.evaluated_players for metric in season_metrics),
                weighted_residual_rho=_weighted_average(rho_values),
                year_signs=_year_signs(season_metrics),
                weighted_quartile_delta=_weighted_average(quartile_values),
            )
        )
    return PeripheralRateSignalAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        metrics=tuple(aggregate),
    )

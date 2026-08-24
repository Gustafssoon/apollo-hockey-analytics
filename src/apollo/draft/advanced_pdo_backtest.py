from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

PDO_SIGNAL_NAMES = (
    "shooting_pct_5v5",
    "save_pct_5v5",
    "pdo_5v5",
)


@dataclass(frozen=True, slots=True)
class AdvancedPDOPlayer:
    player_id: int
    player_name: str
    baseline_goals: float
    baseline_assists: float
    actual_goals: float
    actual_assists: float
    weighted_signals: dict[str, float]


@dataclass(frozen=True, slots=True)
class AdvancedPDOMetric:
    signal_name: str
    evaluated_players: int
    goals_residual_rho: float | None
    goals_quartile_delta: float | None
    assists_residual_rho: float | None
    assists_quartile_delta: float | None
    points_residual_rho: float | None
    points_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class AdvancedPDOBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    metrics: tuple[AdvancedPDOMetric, ...]


@dataclass(frozen=True, slots=True)
class AdvancedPDOAggregateMetric:
    signal_name: str
    player_seasons: int
    weighted_goals_residual_rho: float | None
    goals_year_signs: str
    weighted_goals_quartile_delta: float | None
    weighted_assists_residual_rho: float | None
    assists_year_signs: str
    weighted_assists_quartile_delta: float | None
    weighted_points_residual_rho: float | None
    points_year_signs: str
    weighted_points_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class AdvancedPDOAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[AdvancedPDOAggregateMetric, ...]


def _weighted_average(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def pdo_signal_value(signal_name: str, stats: dict[str, float]) -> float | None:
    shooting = stats.get("shootingPct5v5")
    save = stats.get("skaterSavePct5v5")
    if signal_name == "shooting_pct_5v5":
        return shooting
    if signal_name == "save_pct_5v5":
        return save
    if signal_name == "pdo_5v5":
        if shooting is None or save is None:
            return None
        return shooting + save
    raise ProjectionError(f"Unknown PDO signal: {signal_name}")


def build_weighted_pdo_signals(
    history: tuple[dict[str, float], ...],
    *,
    min_signal_seasons: int = 3,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> dict[str, float]:
    if len(history) > len(season_weights):
        raise ProjectionError("More PDO history seasons than configured season weights")
    if min_signal_seasons < 1:
        raise ProjectionError("min_signal_seasons must be >= 1")

    weighted: dict[str, float] = {}
    for signal_name in PDO_SIGNAL_NAMES:
        values: list[tuple[float, float]] = []
        for index, stats in enumerate(history):
            value = pdo_signal_value(signal_name, stats)
            if value is not None:
                values.append((value, season_weights[index]))
        if len(values) < min_signal_seasons:
            continue
        result = _weighted_average(values)
        if result is not None:
            weighted[signal_name] = result
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


def build_advanced_pdo_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[AdvancedPDOPlayer, ...],
) -> AdvancedPDOBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("PDO screen requires baseline-eligible skaters")

    metrics: list[AdvancedPDOMetric] = []
    for signal_name in PDO_SIGNAL_NAMES:
        eligible = [player for player in players if signal_name in player.weighted_signals]
        signals = [player.weighted_signals[signal_name] for player in eligible]
        goals_residuals = [player.actual_goals - player.baseline_goals for player in eligible]
        assists_residuals = [
            player.actual_assists - player.baseline_assists for player in eligible
        ]
        points_residuals = [
            (player.actual_goals + player.actual_assists)
            - (player.baseline_goals + player.baseline_assists)
            for player in eligible
        ]
        metrics.append(
            AdvancedPDOMetric(
                signal_name=signal_name,
                evaluated_players=len(eligible),
                goals_residual_rho=spearman_rank_correlation(signals, goals_residuals),
                goals_quartile_delta=_quartile_delta(signals, goals_residuals),
                assists_residual_rho=spearman_rank_correlation(signals, assists_residuals),
                assists_quartile_delta=_quartile_delta(signals, assists_residuals),
                points_residual_rho=spearman_rank_correlation(signals, points_residuals),
                points_quartile_delta=_quartile_delta(signals, points_residuals),
            )
        )

    return AdvancedPDOBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        metrics=tuple(metrics),
    )


def _weighted_metric(season_metrics: list[AdvancedPDOMetric], attribute: str) -> float | None:
    values: list[tuple[float, float]] = []
    for metric in season_metrics:
        value = getattr(metric, attribute)
        if value is None or metric.evaluated_players <= 0:
            continue
        values.append((float(value), float(metric.evaluated_players)))
    return _weighted_average(values)


def _year_signs(season_metrics: list[AdvancedPDOMetric], attribute: str) -> str:
    signs: list[str] = []
    for metric in season_metrics:
        value = getattr(metric, attribute)
        if value is None or abs(value) < 0.02:
            signs.append("0")
        elif value > 0:
            signs.append("+")
        else:
            signs.append("-")
    return "".join(signs)


def build_advanced_pdo_aggregate_result(
    results: tuple[AdvancedPDOBacktestResult, ...],
) -> AdvancedPDOAggregateResult:
    if not results:
        raise ProjectionError("PDO aggregate requires at least one season")

    aggregate_metrics: list[AdvancedPDOAggregateMetric] = []
    for signal_name in PDO_SIGNAL_NAMES:
        season_metrics = [
            next(metric for metric in result.metrics if metric.signal_name == signal_name)
            for result in results
        ]
        aggregate_metrics.append(
            AdvancedPDOAggregateMetric(
                signal_name=signal_name,
                player_seasons=sum(metric.evaluated_players for metric in season_metrics),
                weighted_goals_residual_rho=_weighted_metric(
                    season_metrics, "goals_residual_rho"
                ),
                goals_year_signs=_year_signs(season_metrics, "goals_residual_rho"),
                weighted_goals_quartile_delta=_weighted_metric(
                    season_metrics, "goals_quartile_delta"
                ),
                weighted_assists_residual_rho=_weighted_metric(
                    season_metrics, "assists_residual_rho"
                ),
                assists_year_signs=_year_signs(season_metrics, "assists_residual_rho"),
                weighted_assists_quartile_delta=_weighted_metric(
                    season_metrics, "assists_quartile_delta"
                ),
                weighted_points_residual_rho=_weighted_metric(
                    season_metrics, "points_residual_rho"
                ),
                points_year_signs=_year_signs(season_metrics, "points_residual_rho"),
                weighted_points_quartile_delta=_weighted_metric(
                    season_metrics, "points_quartile_delta"
                ),
            )
        )

    return AdvancedPDOAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        metrics=tuple(aggregate_metrics),
    )

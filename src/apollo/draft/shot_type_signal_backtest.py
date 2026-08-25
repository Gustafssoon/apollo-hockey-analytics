from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

SHOT_TYPE_SIGNAL_NAMES = (
    "tip_deflect_shot_share",
    "wrist_shot_share",
    "snap_shot_share",
    "overall_shooting_pct",
    "tip_deflect_shooting_pct",
    "wrist_shooting_pct",
    "snap_shooting_pct",
    "tip_deflect_goal_share",
    "wrist_goal_share",
    "snap_goal_share",
)


@dataclass(frozen=True, slots=True)
class ShotTypeSignalPlayer:
    player_id: int
    player_name: str
    baseline_goals: float
    baseline_assists: float
    actual_goals: float
    actual_assists: float
    weighted_signals: dict[str, float]


@dataclass(frozen=True, slots=True)
class ShotTypeSignalMetric:
    signal_name: str
    evaluated_players: int
    goals_residual_rho: float | None
    goals_quartile_delta: float | None
    points_residual_rho: float | None
    points_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class ShotTypeSignalBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    metrics: tuple[ShotTypeSignalMetric, ...]


@dataclass(frozen=True, slots=True)
class ShotTypeSignalAggregateMetric:
    signal_name: str
    player_seasons: int
    weighted_goals_residual_rho: float | None
    goals_year_signs: str
    weighted_goals_quartile_delta: float | None
    weighted_points_residual_rho: float | None
    points_year_signs: str
    weighted_points_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class ShotTypeSignalAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[ShotTypeSignalAggregateMetric, ...]


def _weighted_average(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def _values(stats: dict[str, float], names: tuple[str, ...]) -> tuple[float, ...] | None:
    values: list[float] = []
    for name in names:
        value = stats.get(name)
        if value is None or value < 0:
            return None
        values.append(value)
    return tuple(values)


def _ratio(numerator: float, denominator: float) -> float | None:
    if numerator < 0 or denominator <= 0:
        return None
    return numerator / denominator


def signal_value(signal_name: str, stats: dict[str, float]) -> float | None:
    total_shots = stats.get("shotTypeShots")
    total_goals = stats.get("shotTypeGoals")

    if signal_name == "overall_shooting_pct":
        value = stats.get("shotTypeShootingPct")
        return value if value is not None and value >= 0 else None

    if signal_name == "wrist_shooting_pct":
        value = stats.get("shootingPctWrist")
        return value if value is not None and value >= 0 else None

    if signal_name == "snap_shooting_pct":
        value = stats.get("shootingPctSnap")
        return value if value is not None and value >= 0 else None

    if signal_name == "wrist_shot_share":
        wrist = stats.get("shotsOnNetWrist")
        if wrist is None or total_shots is None:
            return None
        return _ratio(wrist, total_shots)

    if signal_name == "snap_shot_share":
        snap = stats.get("shotsOnNetSnap")
        if snap is None or total_shots is None:
            return None
        return _ratio(snap, total_shots)

    if signal_name == "wrist_goal_share":
        wrist = stats.get("goalsWrist")
        if wrist is None or total_goals is None:
            return None
        return _ratio(wrist, total_goals)

    if signal_name == "snap_goal_share":
        snap = stats.get("goalsSnap")
        if snap is None or total_goals is None:
            return None
        return _ratio(snap, total_goals)

    tip_deflect_shots = _values(stats, ("shotsOnNetTipIn", "shotsOnNetDeflected"))
    tip_deflect_goals = _values(stats, ("goalsTipIn", "goalsDeflected"))

    if signal_name == "tip_deflect_shot_share":
        if tip_deflect_shots is None or total_shots is None:
            return None
        return _ratio(sum(tip_deflect_shots), total_shots)

    if signal_name == "tip_deflect_shooting_pct":
        if tip_deflect_shots is None or tip_deflect_goals is None:
            return None
        return _ratio(sum(tip_deflect_goals), sum(tip_deflect_shots))

    if signal_name == "tip_deflect_goal_share":
        if tip_deflect_goals is None or total_goals is None:
            return None
        return _ratio(sum(tip_deflect_goals), total_goals)

    raise ProjectionError(f"Unknown shot-type signal: {signal_name}")


def build_weighted_shot_type_signals(
    history: tuple[dict[str, float], ...],
    *,
    min_signal_seasons: int = 3,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> dict[str, float]:
    if len(history) > len(season_weights):
        raise ProjectionError("More shot-type history seasons than configured season weights")
    if min_signal_seasons < 1 or min_signal_seasons > len(season_weights):
        raise ProjectionError(
            f"min_signal_seasons must be between 1 and {len(season_weights)}"
        )

    weighted: dict[str, float] = {}
    for signal_name in SHOT_TYPE_SIGNAL_NAMES:
        values: list[tuple[float, float]] = []
        for index, stats in enumerate(history):
            value = signal_value(signal_name, stats)
            if value is None:
                continue
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


def build_shot_type_signal_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[ShotTypeSignalPlayer, ...],
) -> ShotTypeSignalBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Shot-type signal backtest requires baseline-eligible skaters")

    metrics: list[ShotTypeSignalMetric] = []
    for signal_name in SHOT_TYPE_SIGNAL_NAMES:
        eligible = [player for player in players if signal_name in player.weighted_signals]
        signals = [player.weighted_signals[signal_name] for player in eligible]
        goals_residuals = [player.actual_goals - player.baseline_goals for player in eligible]
        points_residuals = [
            (player.actual_goals + player.actual_assists)
            - (player.baseline_goals + player.baseline_assists)
            for player in eligible
        ]
        metrics.append(
            ShotTypeSignalMetric(
                signal_name=signal_name,
                evaluated_players=len(eligible),
                goals_residual_rho=spearman_rank_correlation(signals, goals_residuals),
                goals_quartile_delta=_quartile_delta(signals, goals_residuals),
                points_residual_rho=spearman_rank_correlation(signals, points_residuals),
                points_quartile_delta=_quartile_delta(signals, points_residuals),
            )
        )

    return ShotTypeSignalBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        metrics=tuple(metrics),
    )


def _weighted_metric(
    season_metrics: list[ShotTypeSignalMetric],
    attribute: str,
) -> float | None:
    values: list[tuple[float, float]] = []
    for metric in season_metrics:
        value = getattr(metric, attribute)
        if value is None or metric.evaluated_players <= 0:
            continue
        values.append((float(value), float(metric.evaluated_players)))
    return _weighted_average(values)


def _year_signs(season_metrics: list[ShotTypeSignalMetric], attribute: str) -> str:
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


def build_shot_type_signal_aggregate_result(
    results: tuple[ShotTypeSignalBacktestResult, ...],
) -> ShotTypeSignalAggregateResult:
    if not results:
        raise ProjectionError("Shot-type signal aggregate requires at least one season")

    aggregate_metrics: list[ShotTypeSignalAggregateMetric] = []
    for signal_name in SHOT_TYPE_SIGNAL_NAMES:
        season_metrics = [
            next(metric for metric in result.metrics if metric.signal_name == signal_name)
            for result in results
        ]
        aggregate_metrics.append(
            ShotTypeSignalAggregateMetric(
                signal_name=signal_name,
                player_seasons=sum(metric.evaluated_players for metric in season_metrics),
                weighted_goals_residual_rho=_weighted_metric(
                    season_metrics, "goals_residual_rho"
                ),
                goals_year_signs=_year_signs(season_metrics, "goals_residual_rho"),
                weighted_goals_quartile_delta=_weighted_metric(
                    season_metrics, "goals_quartile_delta"
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

    return ShotTypeSignalAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        metrics=tuple(aggregate_metrics),
    )

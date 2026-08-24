from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

ADVANCED_SIGNAL_NAMES = (
    "cf60_5v5",
    "ff60_5v5",
    "sat_pct_5v5",
    "usat_pct_5v5",
    "sat_relative_5v5",
    "usat_relative_5v5",
    "zone_start_pct_5v5",
    "shooting_pct_5v5",
    "toi_per_game_5v5",
)


@dataclass(frozen=True, slots=True)
class AdvancedSignalPlayer:
    player_id: int
    player_name: str
    baseline_goals: float
    baseline_shots: float
    actual_goals: float
    actual_shots: float
    weighted_signals: dict[str, float]


@dataclass(frozen=True, slots=True)
class AdvancedSignalMetric:
    signal_name: str
    evaluated_players: int
    goals_residual_rho: float | None
    goals_quartile_delta: float | None
    shots_residual_rho: float | None
    shots_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class AdvancedSignalBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    metrics: tuple[AdvancedSignalMetric, ...]


@dataclass(frozen=True, slots=True)
class AdvancedSignalAggregateMetric:
    signal_name: str
    player_seasons: int
    weighted_goals_residual_rho: float | None
    goals_year_signs: str
    weighted_goals_quartile_delta: float | None
    weighted_shots_residual_rho: float | None
    shots_year_signs: str
    weighted_shots_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class AdvancedSignalAggregateResult:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[AdvancedSignalAggregateMetric, ...]


def _weighted_average(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def signal_value(
    signal_name: str,
    *,
    games_played: float,
    stats: dict[str, float],
) -> float | None:
    if games_played <= 0:
        return None

    if signal_name in {"cf60_5v5", "ff60_5v5"}:
        toi_per_game = stats.get("timeOnIcePerGame5v5")
        attempts_name = (
            "shotAttemptsFor5v5"
            if signal_name == "cf60_5v5"
            else "unblockedShotAttemptsFor5v5"
        )
        attempts = stats.get(attempts_name)
        if toi_per_game is None or attempts is None or toi_per_game <= 0:
            return None
        total_toi_seconds = toi_per_game * games_played
        return attempts / total_toi_seconds * 3600.0

    direct_map = {
        "sat_pct_5v5": "shotAttemptsPct5v5",
        "usat_pct_5v5": "unblockedShotAttemptsPct5v5",
        "sat_relative_5v5": "shotAttemptsRelative5v5",
        "usat_relative_5v5": "unblockedShotAttemptsRelative5v5",
        "zone_start_pct_5v5": "zoneStartPct5v5",
        "shooting_pct_5v5": "shootingPct5v5",
        "toi_per_game_5v5": "timeOnIcePerGame5v5",
    }
    stat_name = direct_map.get(signal_name)
    if stat_name is None:
        raise ProjectionError(f"Unknown advanced signal: {signal_name}")
    return stats.get(stat_name)


def build_weighted_signals(
    history: tuple[tuple[int, float, dict[str, float]], ...],
    *,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> dict[str, float]:
    if len(history) > len(season_weights):
        raise ProjectionError("More advanced history seasons than configured season weights")

    weighted: dict[str, float] = {}
    for signal_name in ADVANCED_SIGNAL_NAMES:
        values: list[tuple[float, float]] = []
        for index, (_, games_played, stats) in enumerate(history):
            value = signal_value(
                signal_name,
                games_played=games_played,
                stats=stats,
            )
            if value is None:
                continue
            values.append((value, season_weights[index]))
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


def build_advanced_signal_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[AdvancedSignalPlayer, ...],
) -> AdvancedSignalBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Advanced signal backtest requires baseline-eligible skaters")

    metrics: list[AdvancedSignalMetric] = []
    for signal_name in ADVANCED_SIGNAL_NAMES:
        eligible = [player for player in players if signal_name in player.weighted_signals]
        signals = [player.weighted_signals[signal_name] for player in eligible]
        goals_residuals = [player.actual_goals - player.baseline_goals for player in eligible]
        shots_residuals = [player.actual_shots - player.baseline_shots for player in eligible]
        metrics.append(
            AdvancedSignalMetric(
                signal_name=signal_name,
                evaluated_players=len(eligible),
                goals_residual_rho=spearman_rank_correlation(signals, goals_residuals),
                goals_quartile_delta=_quartile_delta(signals, goals_residuals),
                shots_residual_rho=spearman_rank_correlation(signals, shots_residuals),
                shots_quartile_delta=_quartile_delta(signals, shots_residuals),
            )
        )

    return AdvancedSignalBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        metrics=tuple(metrics),
    )


def _weighted_metric(
    season_metrics: list[AdvancedSignalMetric],
    attribute: str,
) -> float | None:
    values: list[tuple[float, float]] = []
    for metric in season_metrics:
        value = getattr(metric, attribute)
        if value is None or metric.evaluated_players <= 0:
            continue
        values.append((float(value), float(metric.evaluated_players)))
    return _weighted_average(values)


def _year_signs(season_metrics: list[AdvancedSignalMetric], attribute: str) -> str:
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


def build_advanced_signal_aggregate_result(
    results: tuple[AdvancedSignalBacktestResult, ...],
) -> AdvancedSignalAggregateResult:
    if not results:
        raise ProjectionError("Advanced signal aggregate requires at least one season")

    aggregate_metrics: list[AdvancedSignalAggregateMetric] = []
    for signal_name in ADVANCED_SIGNAL_NAMES:
        season_metrics = [
            next(metric for metric in result.metrics if metric.signal_name == signal_name)
            for result in results
        ]
        aggregate_metrics.append(
            AdvancedSignalAggregateMetric(
                signal_name=signal_name,
                player_seasons=sum(metric.evaluated_players for metric in season_metrics),
                weighted_goals_residual_rho=_weighted_metric(
                    season_metrics, "goals_residual_rho"
                ),
                goals_year_signs=_year_signs(season_metrics, "goals_residual_rho"),
                weighted_goals_quartile_delta=_weighted_metric(
                    season_metrics, "goals_quartile_delta"
                ),
                weighted_shots_residual_rho=_weighted_metric(
                    season_metrics, "shots_residual_rho"
                ),
                shots_year_signs=_year_signs(season_metrics, "shots_residual_rho"),
                weighted_shots_quartile_delta=_weighted_metric(
                    season_metrics, "shots_quartile_delta"
                ),
            )
        )

    return AdvancedSignalAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        metrics=tuple(aggregate_metrics),
    )

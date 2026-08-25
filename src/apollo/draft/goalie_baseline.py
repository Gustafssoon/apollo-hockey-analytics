from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import ProjectionError, previous_seasons

GOALIE_BASELINE_VERSION = "apollo-goalie-baseline-v0.1"
GOALIE_SEASON_WEIGHTS = (0.60, 0.30, 0.10)
GOALIE_TOTAL_STATS = ("wins", "saves", "goalsAgainst", "shutouts")
GOALIE_RATIO_STATS = ("savePctg", "goalsAgainstAvg")
GOALIE_BACKTEST_STATS = ("gamesStarted", *GOALIE_TOTAL_STATS, *GOALIE_RATIO_STATS)
GOALIE_REQUIRED_SOURCE_STATS = (
    "gamesStarted",
    *GOALIE_TOTAL_STATS,
    *GOALIE_RATIO_STATS,
)


@dataclass(frozen=True, slots=True)
class GoalieProjection:
    projected_starts: float
    stats: dict[str, float]
    source_seasons: tuple[int, ...]
    model_version: str = GOALIE_BASELINE_VERSION


@dataclass(frozen=True, slots=True)
class GoalieBacktestPlayer:
    player_id: int
    player_name: str
    projected_starts: float
    actual_starts: float
    projected_stats: dict[str, float]
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class GoalieBacktestMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None
    oracle_starts_mae: float | None
    oracle_starts_spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class GoalieBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    evaluated_goalies: int
    actual_eligible_goalies: int
    coverage: float
    metrics: tuple[GoalieBacktestMetric, ...]
    model_version: str = GOALIE_BASELINE_VERSION


@dataclass(frozen=True, slots=True)
class GoalieBaselineAggregate:
    target_seasons: tuple[int, ...]
    player_seasons: int
    metrics: tuple[GoalieBacktestMetric, ...]


def _weighted(values: tuple[float, ...]) -> float:
    if len(values) != len(GOALIE_SEASON_WEIGHTS):
        raise ProjectionError("Goalie baseline requires exactly three source seasons")
    return sum(value * weight for value, weight in zip(values, GOALIE_SEASON_WEIGHTS, strict=True))


def build_goalie_projection(
    history: tuple[tuple[int, dict[str, float]], ...],
) -> GoalieProjection:
    if len(history) != len(GOALIE_SEASON_WEIGHTS):
        raise ProjectionError("Goalie baseline requires exactly three source seasons")

    source_seasons = tuple(season for season, _ in history)
    for _, stats in history:
        starts = stats.get("gamesStarted", 0.0)
        if starts <= 0:
            raise ProjectionError("Goalie baseline requires positive starts in all source seasons")
        if any(stat_name not in stats for stat_name in GOALIE_REQUIRED_SOURCE_STATS):
            raise ProjectionError("Goalie baseline source season is missing required stats")

    projected_starts = min(82.0, max(0.0, _weighted(tuple(stats["gamesStarted"] for _, stats in history))))
    projected_stats: dict[str, float] = {}
    for stat_name in GOALIE_TOTAL_STATS:
        projected_rate = _weighted(
            tuple(stats[stat_name] / stats["gamesStarted"] for _, stats in history)
        )
        projected_stats[stat_name] = projected_rate * projected_starts
    for stat_name in GOALIE_RATIO_STATS:
        projected_stats[stat_name] = _weighted(tuple(stats[stat_name] for _, stats in history))

    return GoalieProjection(
        projected_starts=projected_starts,
        stats=projected_stats,
        source_seasons=source_seasons,
    )


def _projected_value(player: GoalieBacktestPlayer, stat_name: str) -> float:
    if stat_name == "gamesStarted":
        return player.projected_starts
    return player.projected_stats[stat_name]


def _actual_value(player: GoalieBacktestPlayer, stat_name: str) -> float:
    if stat_name == "gamesStarted":
        return player.actual_starts
    return player.actual_stats[stat_name]


def _oracle_starts_value(player: GoalieBacktestPlayer, stat_name: str) -> float:
    if stat_name in GOALIE_RATIO_STATS:
        return player.projected_stats[stat_name]
    if stat_name == "gamesStarted":
        return player.actual_starts
    if player.projected_starts <= 0:
        raise ProjectionError("Goalie oracle-start metric requires positive projected starts")
    return player.projected_stats[stat_name] / player.projected_starts * player.actual_starts


def build_goalie_backtest_result(
    *,
    target_season: int,
    players: tuple[GoalieBacktestPlayer, ...],
    actual_eligible_goalies: int,
) -> GoalieBacktestResult:
    if actual_eligible_goalies <= 0 or not players:
        raise ProjectionError("Goalie backtest requires eligible goalies")

    metrics: list[GoalieBacktestMetric] = []
    for stat_name in GOALIE_BACKTEST_STATS:
        projected = [_projected_value(player, stat_name) for player in players]
        actual = [_actual_value(player, stat_name) for player in players]
        mae = sum(abs(p - a) for p, a in zip(projected, actual, strict=True)) / len(players)
        oracle_mae: float | None = None
        oracle_rho: float | None = None
        if stat_name != "gamesStarted":
            oracle = [_oracle_starts_value(player, stat_name) for player in players]
            oracle_mae = sum(abs(p - a) for p, a in zip(oracle, actual, strict=True)) / len(players)
            oracle_rho = spearman_rank_correlation(oracle, actual)
        metrics.append(
            GoalieBacktestMetric(
                stat_name=stat_name,
                mae=mae,
                spearman_rho=spearman_rank_correlation(projected, actual),
                oracle_starts_mae=oracle_mae,
                oracle_starts_spearman_rho=oracle_rho,
            )
        )

    return GoalieBacktestResult(
        target_season=target_season,
        source_seasons=previous_seasons(target_season, 3),
        evaluated_goalies=len(players),
        actual_eligible_goalies=actual_eligible_goalies,
        coverage=len(players) / actual_eligible_goalies,
        metrics=tuple(metrics),
    )


def build_goalie_baseline_aggregate(
    results: tuple[GoalieBacktestResult, ...],
) -> GoalieBaselineAggregate:
    if not results:
        raise ProjectionError("Goalie baseline aggregate requires season results")
    total_n = sum(result.evaluated_goalies for result in results)
    if total_n <= 0:
        raise ProjectionError("Goalie baseline aggregate requires evaluated goalies")

    metrics: list[GoalieBacktestMetric] = []
    for stat_name in GOALIE_BACKTEST_STATS:
        season_metrics = [next(metric for metric in result.metrics if metric.stat_name == stat_name) for result in results]
        mae = sum(metric.mae * result.evaluated_goalies for metric, result in zip(season_metrics, results, strict=True)) / total_n
        rho_pairs = [(metric.spearman_rho, result.evaluated_goalies) for metric, result in zip(season_metrics, results, strict=True) if metric.spearman_rho is not None]
        rho = None if not rho_pairs else sum(float(value) * weight for value, weight in rho_pairs) / sum(weight for _, weight in rho_pairs)
        oracle_pairs = [(metric.oracle_starts_mae, result.evaluated_goalies) for metric, result in zip(season_metrics, results, strict=True) if metric.oracle_starts_mae is not None]
        oracle_mae = None if not oracle_pairs else sum(float(value) * weight for value, weight in oracle_pairs) / sum(weight for _, weight in oracle_pairs)
        oracle_rho_pairs = [(metric.oracle_starts_spearman_rho, result.evaluated_goalies) for metric, result in zip(season_metrics, results, strict=True) if metric.oracle_starts_spearman_rho is not None]
        oracle_rho = None if not oracle_rho_pairs else sum(float(value) * weight for value, weight in oracle_rho_pairs) / sum(weight for _, weight in oracle_rho_pairs)
        metrics.append(
            GoalieBacktestMetric(
                stat_name=stat_name,
                mae=mae,
                spearman_rho=rho,
                oracle_starts_mae=oracle_mae,
                oracle_starts_spearman_rho=oracle_rho,
            )
        )
    return GoalieBaselineAggregate(
        target_seasons=tuple(result.target_season for result in results),
        player_seasons=total_n,
        metrics=tuple(metrics),
    )

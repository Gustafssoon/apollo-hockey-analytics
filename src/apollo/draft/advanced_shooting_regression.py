from dataclasses import dataclass

from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import ProjectionError
from apollo.draft.shooting_context import correction_factor

CORRECTION_STRENGTHS = (0.10, 0.25, 0.50)
CORRECTION_SCOPES = ("goals", "offense")


@dataclass(frozen=True, slots=True)
class ShootingRegressionPlayer:
    player_id: int
    player_name: str
    baseline_goals: float
    baseline_assists: float
    actual_goals: float
    actual_assists: float
    shooting_context_ratio: float


@dataclass(frozen=True, slots=True)
class ShootingRegressionMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class ShootingRegressionStrategyResult:
    strategy_name: str
    scope: str | None
    strength: float | None
    metrics: tuple[ShootingRegressionMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class ShootingRegressionBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    evaluated_players: int
    strategies: tuple[ShootingRegressionStrategyResult, ...]


@dataclass(frozen=True, slots=True)
class ShootingRegressionAggregateMetric:
    stat_name: str
    weighted_mae: float
    weighted_rho: float | None


@dataclass(frozen=True, slots=True)
class ShootingRegressionAggregateStrategy:
    strategy_name: str
    scope: str | None
    strength: float | None
    metrics: tuple[ShootingRegressionAggregateMetric, ...]
    points_improved_years: int
    worst_points_mae_gain: float
    top25_overlap_rate: float


@dataclass(frozen=True, slots=True)
class ShootingRegressionAggregateResult:
    target_seasons: tuple[int, ...]
    player_seasons: int
    baseline_player_seasons: int
    strategies: tuple[ShootingRegressionAggregateStrategy, ...]


def _strategy_projection(
    player: ShootingRegressionPlayer,
    scope: str | None,
    strength: float | None,
) -> tuple[float, float]:
    if scope is None or strength is None:
        return player.baseline_goals, player.baseline_assists
    factor = correction_factor(player.shooting_context_ratio, strength)
    goals = player.baseline_goals * factor
    assists = player.baseline_assists * factor if scope == "offense" else player.baseline_assists
    return goals, assists


def _top_k_overlaps(
    players: tuple[ShootingRegressionPlayer, ...],
    projected_points: list[float],
) -> tuple[TopKOverlap, ...]:
    projected = sorted(
        zip((player.player_id for player in players), projected_points, strict=True),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    actual = sorted(
        (
            (player.player_id, player.actual_goals + player.actual_assists)
            for player in players
        ),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    overlaps: list[TopKOverlap] = []
    for requested_k in TOP_K_CUTOFFS:
        compared_k = min(requested_k, len(players))
        projected_ids = {player_id for player_id, _ in projected[:compared_k]}
        actual_ids = {player_id for player_id, _ in actual[:compared_k]}
        overlap = len(projected_ids & actual_ids)
        overlaps.append(
            TopKOverlap(
                requested_k=requested_k,
                compared_k=compared_k,
                overlap=overlap,
                overlap_rate=overlap / compared_k,
            )
        )
    return tuple(overlaps)


def build_shooting_regression_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[ShootingRegressionPlayer, ...],
) -> ShootingRegressionBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Shooting regression backtest requires baseline-eligible skaters")
    if not players:
        raise ProjectionError("No skaters have complete shooting-context history")

    candidates: tuple[tuple[str, str | None, float | None], ...] = (
        ("baseline_v04", None, None),
        *(
            (f"sh_{scope}_{int(strength * 100)}", scope, strength)
            for scope in CORRECTION_SCOPES
            for strength in CORRECTION_STRENGTHS
        ),
    )
    strategies: list[ShootingRegressionStrategyResult] = []
    for strategy_name, scope, strength in candidates:
        projected_goals: list[float] = []
        projected_assists: list[float] = []
        for player in players:
            goals, assists = _strategy_projection(player, scope, strength)
            projected_goals.append(goals)
            projected_assists.append(assists)
        projected_points = [
            goals + assists
            for goals, assists in zip(projected_goals, projected_assists, strict=True)
        ]
        actual_goals = [player.actual_goals for player in players]
        actual_assists = [player.actual_assists for player in players]
        actual_points = [
            player.actual_goals + player.actual_assists for player in players
        ]
        metrics: list[ShootingRegressionMetric] = []
        for stat_name, projected, actual in (
            ("goals", projected_goals, actual_goals),
            ("assists", projected_assists, actual_assists),
            ("points", projected_points, actual_points),
        ):
            mae = sum(
                abs(projected_value - actual_value)
                for projected_value, actual_value in zip(projected, actual, strict=True)
            ) / len(players)
            metrics.append(
                ShootingRegressionMetric(
                    stat_name=stat_name,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected, actual),
                )
            )
        strategies.append(
            ShootingRegressionStrategyResult(
                strategy_name=strategy_name,
                scope=scope,
                strength=strength,
                metrics=tuple(metrics),
                top_k_points=_top_k_overlaps(players, projected_points),
            )
        )

    return ShootingRegressionBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        evaluated_players=len(players),
        strategies=tuple(strategies),
    )


def _metric(strategy: ShootingRegressionStrategyResult, stat_name: str) -> ShootingRegressionMetric:
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float | None:
    total = sum(weight for _, weight in values)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in values) / total


def build_shooting_regression_aggregate_result(
    results: tuple[ShootingRegressionBacktestResult, ...],
) -> ShootingRegressionAggregateResult:
    if not results:
        raise ProjectionError("Shooting regression aggregate requires at least one season")

    names = tuple(strategy.strategy_name for strategy in results[0].strategies)
    aggregate: list[ShootingRegressionAggregateStrategy] = []
    for strategy_name in names:
        season_strategies = [
            next(strategy for strategy in result.strategies if strategy.strategy_name == strategy_name)
            for result in results
        ]
        baseline_season_strategies = [
            next(strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v04")
            for result in results
        ]
        metrics: list[ShootingRegressionAggregateMetric] = []
        for stat_name in ("goals", "assists", "points"):
            mae = _weighted(
                [
                    (_metric(strategy, stat_name).mae, result.evaluated_players)
                    for strategy, result in zip(season_strategies, results, strict=True)
                ]
            )
            rho_values = [
                (_metric(strategy, stat_name).spearman_rho, result.evaluated_players)
                for strategy, result in zip(season_strategies, results, strict=True)
                if _metric(strategy, stat_name).spearman_rho is not None
            ]
            rho = _weighted([(float(value), weight) for value, weight in rho_values])
            metrics.append(
                ShootingRegressionAggregateMetric(
                    stat_name=stat_name,
                    weighted_mae=float(mae),
                    weighted_rho=rho,
                )
            )
        year_gains = [
            _metric(baseline, "points").mae - _metric(strategy, "points").mae
            for strategy, baseline in zip(
                season_strategies,
                baseline_season_strategies,
                strict=True,
            )
        ]
        top25 = _weighted(
            [
                (
                    next(
                        overlap.overlap_rate
                        for overlap in strategy.top_k_points
                        if overlap.requested_k == 25
                    ),
                    1,
                )
                for strategy in season_strategies
            ]
        )
        first = season_strategies[0]
        aggregate.append(
            ShootingRegressionAggregateStrategy(
                strategy_name=strategy_name,
                scope=first.scope,
                strength=first.strength,
                metrics=tuple(metrics),
                points_improved_years=sum(gain > 0 for gain in year_gains),
                worst_points_mae_gain=min(year_gains),
                top25_overlap_rate=float(top25),
            )
        )

    return ShootingRegressionAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        player_seasons=sum(result.evaluated_players for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        strategies=tuple(aggregate),
    )

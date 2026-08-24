from dataclasses import dataclass

from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

RATE_CORRECTION_STRENGTHS = (0.05, 0.10, 0.20)
MIN_CORRECTION_FACTOR = 0.80
MAX_CORRECTION_FACTOR = 1.20


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionPlayer:
    player_id: int
    player_name: str
    baseline_goals: float
    baseline_assists: float
    actual_goals: float
    actual_assists: float
    g60_ratio: float
    a60_ratio: float
    secondary_a60_ratio: float


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionStrategyResult:
    strategy_name: str
    goal_strength: float | None
    assist_signal: str | None
    assist_strength: float | None
    metrics: tuple[ScoringRateRegressionMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    baseline_eligible_players: int
    evaluated_players: int
    strategies: tuple[ScoringRateRegressionStrategyResult, ...]


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionAggregateMetric:
    stat_name: str
    weighted_mae: float
    weighted_rho: float | None


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionAggregateStrategy:
    strategy_name: str
    goal_strength: float | None
    assist_signal: str | None
    assist_strength: float | None
    metrics: tuple[ScoringRateRegressionAggregateMetric, ...]
    points_improved_years: int
    worst_points_mae_gain: float
    top25_overlap_rate: float


@dataclass(frozen=True, slots=True)
class ScoringRateRegressionAggregateResult:
    target_seasons: tuple[int, ...]
    player_seasons: int
    baseline_player_seasons: int
    strategies: tuple[ScoringRateRegressionAggregateStrategy, ...]


def correction_factor(context_ratio: float, strength: float) -> float:
    if context_ratio < 0:
        raise ProjectionError("Scoring-rate context ratio must be non-negative")
    if strength < 0:
        raise ProjectionError("Scoring-rate correction strength must be non-negative")
    factor = 1.0 - strength * (context_ratio - 1.0)
    return min(MAX_CORRECTION_FACTOR, max(MIN_CORRECTION_FACTOR, factor))


def build_rate_context_ratio(
    history: tuple[tuple[float, float], ...],
    *,
    min_signal_seasons: int = 3,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> float | None:
    if len(history) > len(season_weights):
        raise ProjectionError("More scoring-rate history seasons than configured weights")
    if min_signal_seasons < 1 or min_signal_seasons > len(season_weights):
        raise ProjectionError(
            f"min_signal_seasons must be between 1 and {len(season_weights)}"
        )

    values: list[tuple[float, float]] = []
    for index, (rate, prior_rate) in enumerate(history):
        if rate < 0 or prior_rate <= 0:
            continue
        values.append((rate / prior_rate, season_weights[index]))
    if len(values) < min_signal_seasons:
        return None
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        return None
    return sum(value * weight for value, weight in values) / weight_sum


def _candidate_specs() -> tuple[tuple[str, float | None, str | None, float | None], ...]:
    candidates: list[tuple[str, float | None, str | None, float | None]] = [
        ("baseline_v05", None, None, None)
    ]
    for strength in RATE_CORRECTION_STRENGTHS:
        pct = int(strength * 100)
        candidates.extend(
            (
                (f"g60_{pct}", strength, None, None),
                (f"a60_{pct}", None, "a60", strength),
                (f"secondary_a60_{pct}", None, "secondary_a60", strength),
                (f"g60_secondary_{pct}", strength, "secondary_a60", strength),
            )
        )
    return tuple(candidates)


def _strategy_projection(
    player: ScoringRateRegressionPlayer,
    *,
    goal_strength: float | None,
    assist_signal: str | None,
    assist_strength: float | None,
) -> tuple[float, float]:
    goals = player.baseline_goals
    assists = player.baseline_assists
    if goal_strength is not None:
        goals *= correction_factor(player.g60_ratio, goal_strength)
    if assist_signal is not None and assist_strength is not None:
        if assist_signal == "a60":
            ratio = player.a60_ratio
        elif assist_signal == "secondary_a60":
            ratio = player.secondary_a60_ratio
        else:
            raise ProjectionError(f"Unknown assist scoring-rate signal: {assist_signal}")
        assists *= correction_factor(ratio, assist_strength)
    return goals, assists


def _top_k_overlaps(
    players: tuple[ScoringRateRegressionPlayer, ...],
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


def build_scoring_rate_regression_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    baseline_eligible_players: int,
    players: tuple[ScoringRateRegressionPlayer, ...],
) -> ScoringRateRegressionBacktestResult:
    if baseline_eligible_players <= 0:
        raise ProjectionError("Scoring-rate regression requires baseline-eligible skaters")
    if not players:
        raise ProjectionError("No skaters have complete scoring-rate history")

    strategies: list[ScoringRateRegressionStrategyResult] = []
    actual_goals = [player.actual_goals for player in players]
    actual_assists = [player.actual_assists for player in players]
    actual_points = [player.actual_goals + player.actual_assists for player in players]

    for strategy_name, goal_strength, assist_signal, assist_strength in _candidate_specs():
        projected_goals: list[float] = []
        projected_assists: list[float] = []
        for player in players:
            goals, assists = _strategy_projection(
                player,
                goal_strength=goal_strength,
                assist_signal=assist_signal,
                assist_strength=assist_strength,
            )
            projected_goals.append(goals)
            projected_assists.append(assists)
        projected_points = [
            goals + assists
            for goals, assists in zip(projected_goals, projected_assists, strict=True)
        ]

        metrics: list[ScoringRateRegressionMetric] = []
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
                ScoringRateRegressionMetric(
                    stat_name=stat_name,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected, actual),
                )
            )
        strategies.append(
            ScoringRateRegressionStrategyResult(
                strategy_name=strategy_name,
                goal_strength=goal_strength,
                assist_signal=assist_signal,
                assist_strength=assist_strength,
                metrics=tuple(metrics),
                top_k_points=_top_k_overlaps(players, projected_points),
            )
        )

    return ScoringRateRegressionBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        evaluated_players=len(players),
        strategies=tuple(strategies),
    )


def _metric(
    strategy: ScoringRateRegressionStrategyResult,
    stat_name: str,
) -> ScoringRateRegressionMetric:
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float | None:
    total = sum(weight for _, weight in values)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in values) / total


def build_scoring_rate_regression_aggregate_result(
    results: tuple[ScoringRateRegressionBacktestResult, ...],
) -> ScoringRateRegressionAggregateResult:
    if not results:
        raise ProjectionError("Scoring-rate regression aggregate requires at least one season")

    names = tuple(strategy.strategy_name for strategy in results[0].strategies)
    aggregate: list[ScoringRateRegressionAggregateStrategy] = []
    for strategy_name in names:
        season_strategies = [
            next(strategy for strategy in result.strategies if strategy.strategy_name == strategy_name)
            for result in results
        ]
        baseline_strategies = [
            next(strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v05")
            for result in results
        ]
        metrics: list[ScoringRateRegressionAggregateMetric] = []
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
                ScoringRateRegressionAggregateMetric(
                    stat_name=stat_name,
                    weighted_mae=float(mae),
                    weighted_rho=rho,
                )
            )

        year_gains = [
            _metric(baseline, "points").mae - _metric(strategy, "points").mae
            for strategy, baseline in zip(
                season_strategies,
                baseline_strategies,
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
            ScoringRateRegressionAggregateStrategy(
                strategy_name=strategy_name,
                goal_strength=first.goal_strength,
                assist_signal=first.assist_signal,
                assist_strength=first.assist_strength,
                metrics=tuple(metrics),
                points_improved_years=sum(gain > 0 for gain in year_gains),
                worst_points_mae_gain=min(year_gains),
                top25_overlap_rate=float(top25),
            )
        )

    return ScoringRateRegressionAggregateResult(
        target_seasons=tuple(result.target_season for result in results),
        player_seasons=sum(result.evaluated_players for result in results),
        baseline_player_seasons=sum(result.baseline_eligible_players for result in results),
        strategies=tuple(aggregate),
    )

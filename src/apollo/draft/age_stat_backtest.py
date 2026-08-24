from dataclasses import dataclass

from apollo.draft.age_backtest import AGE_CURVE_STRATEGIES, age_adjusted_rate
from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError

AGE_STAT_NAMES = ("points", *SKATER_PROJECTION_STATS)


@dataclass(frozen=True, slots=True)
class AgeStatHistorySeason:
    source_age: float
    games_played: float
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class AgeStatBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    projected_games: float
    target_age: float
    history: tuple[AgeStatHistorySeason, ...]
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class AgeStatStrategyResult:
    stat_name: str
    strategy_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class AgeStatBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    base_eligible_players: int
    evaluated_players: int
    birth_date_coverage: float
    metrics: tuple[AgeStatStrategyResult, ...]


def _stat_value(stats: dict[str, float], stat_name: str) -> float:
    if stat_name == "points":
        return stats["goals"] + stats["assists"]
    return stats[stat_name]


def build_age_stat_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[AgeStatBacktestPlayer, ...],
    base_eligible_players: int,
    season_weights: tuple[float, ...],
) -> AgeStatBacktestResult:
    if not players:
        raise ProjectionError("Age stat shootout requires at least one player with birth date")
    if base_eligible_players < len(players):
        raise ProjectionError("base_eligible_players cannot be smaller than evaluated players")

    metrics: list[AgeStatStrategyResult] = []
    for stat_name in AGE_STAT_NAMES:
        actual_values = [_stat_value(player.actual_stats, stat_name) for player in players]
        for strategy in AGE_CURVE_STRATEGIES:
            projected_values: list[float] = []
            for player in players:
                weighted_rates: list[tuple[float, float]] = []
                for index, season in enumerate(player.history):
                    if season.games_played <= 0 or index >= len(season_weights):
                        continue
                    observed_rate = _stat_value(season.stats, stat_name) / season.games_played
                    weighted_rates.append(
                        (
                            age_adjusted_rate(
                                observed_rate=observed_rate,
                                source_age=season.source_age,
                                target_age=player.target_age,
                                position=player.position,
                                strategy=strategy,
                            ),
                            season_weights[index],
                        )
                    )
                weight_sum = sum(weight for _, weight in weighted_rates)
                if weight_sum <= 0:
                    raise ProjectionError(
                        f"Age stat shootout has no usable history for {player.player_name}"
                    )
                projected_rate = (
                    sum(rate * weight for rate, weight in weighted_rates) / weight_sum
                )
                projected_values.append(projected_rate * player.projected_games)

            mae = sum(
                abs(projected - actual)
                for projected, actual in zip(projected_values, actual_values, strict=True)
            ) / len(players)
            metrics.append(
                AgeStatStrategyResult(
                    stat_name=stat_name,
                    strategy_name=strategy.name,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected_values, actual_values),
                )
            )

    return AgeStatBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        base_eligible_players=base_eligible_players,
        evaluated_players=len(players),
        birth_date_coverage=len(players) / base_eligible_players,
        metrics=tuple(metrics),
    )

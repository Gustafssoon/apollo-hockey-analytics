from dataclasses import dataclass
from math import sqrt

from apollo.draft.aging import AgeCurve as AgeCurveStrategy
from apollo.draft.aging import adjust_rate_between_ages
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import ProjectionError


@dataclass(frozen=True, slots=True)
class AgeBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    projected_games: float
    target_age: float
    history: tuple[tuple[float, float, float], ...]
    actual_points: float


@dataclass(frozen=True, slots=True)
class AgeStrategyResult:
    name: str
    points_mae: float
    points_rmse: float
    points_spearman_rho: float | None
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class AgeBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    base_eligible_players: int
    evaluated_players: int
    birth_date_coverage: float
    strategies: tuple[AgeStrategyResult, ...]


AGE_CURVE_STRATEGIES = (
    AgeCurveStrategy("neutral", 27.5, 28.5, 0.0, 0.0),
    AgeCurveStrategy("conservative", 27.5, 28.5, 0.005, 0.010),
    AgeCurveStrategy("gentle", 27.5, 28.5, 0.010, 0.0125),
    AgeCurveStrategy("asymmetric", 27.5, 28.5, 0.010, 0.0175),
    AgeCurveStrategy("medium", 27.5, 28.5, 0.015, 0.020),
    AgeCurveStrategy("late_peak", 28.0, 29.0, 0.010, 0.015),
)


def age_adjusted_rate(
    *,
    observed_rate: float,
    source_age: float,
    target_age: float,
    position: str,
    strategy: AgeCurveStrategy,
) -> float:
    return adjust_rate_between_ages(
        observed_rate=observed_rate,
        source_age=source_age,
        target_age=target_age,
        position=position,
        curve=strategy,
    )


def _top_k_overlaps(
    projected_order: list[AgeBacktestPlayer],
    actual_order: list[AgeBacktestPlayer],
) -> tuple[TopKOverlap, ...]:
    overlaps: list[TopKOverlap] = []
    for requested_k in TOP_K_CUTOFFS:
        compared_k = min(requested_k, len(projected_order))
        projected_ids = {player.player_id for player in projected_order[:compared_k]}
        actual_ids = {player.player_id for player in actual_order[:compared_k]}
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


def build_age_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[AgeBacktestPlayer, ...],
    base_eligible_players: int,
    season_weights: tuple[float, ...],
) -> AgeBacktestResult:
    if not players:
        raise ProjectionError("Age baseline shootout requires at least one player with birth date")
    if base_eligible_players < len(players):
        raise ProjectionError("base_eligible_players cannot be smaller than evaluated players")

    actual_points = [player.actual_points for player in players]
    actual_order = sorted(
        players,
        key=lambda player: (player.actual_points, player.player_id),
        reverse=True,
    )
    results: list[AgeStrategyResult] = []

    for strategy in AGE_CURVE_STRATEGIES:
        projected_points: list[float] = []
        for player in players:
            weighted_rates: list[tuple[float, float]] = []
            for index, (source_age, games_played, points) in enumerate(player.history):
                if games_played <= 0 or index >= len(season_weights):
                    continue
                rate = points / games_played
                weighted_rates.append(
                    (
                        age_adjusted_rate(
                            observed_rate=rate,
                            source_age=source_age,
                            target_age=player.target_age,
                            position=player.position,
                            strategy=strategy,
                        ),
                        season_weights[index],
                    )
                )
            weight_sum = sum(weight for _, weight in weighted_rates)
            if weight_sum <= 0:
                raise ProjectionError(f"Age shootout has no usable history for {player.player_name}")
            projected_rate = sum(rate * weight for rate, weight in weighted_rates) / weight_sum
            projected_points.append(projected_rate * player.projected_games)

        errors = [
            projected - actual
            for projected, actual in zip(projected_points, actual_points, strict=True)
        ]
        points_mae = sum(abs(error) for error in errors) / len(errors)
        points_rmse = sqrt(sum(error * error for error in errors) / len(errors))
        projected_by_id = {
            player.player_id: points
            for player, points in zip(players, projected_points, strict=True)
        }
        projected_order = sorted(
            players,
            key=lambda player: (projected_by_id[player.player_id], player.player_id),
            reverse=True,
        )
        results.append(
            AgeStrategyResult(
                name=strategy.name,
                points_mae=points_mae,
                points_rmse=points_rmse,
                points_spearman_rho=spearman_rank_correlation(projected_points, actual_points),
                top_k_points=_top_k_overlaps(projected_order, actual_order),
            )
        )

    return AgeBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        base_eligible_players=base_eligible_players,
        evaluated_players=len(players),
        birth_date_coverage=len(players) / base_eligible_players,
        strategies=tuple(results),
    )

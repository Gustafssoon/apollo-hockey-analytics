from dataclasses import dataclass
from statistics import median

from apollo.draft.availability import STANDARD_NHL_GAMES
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError


@dataclass(frozen=True, slots=True)
class GPBacktestPlayer:
    player_id: int
    player_name: str
    actual_games: float
    history_games: tuple[float, ...]
    projected_points_per_game: float
    actual_points: float


@dataclass(frozen=True, slots=True)
class GPStrategyResult:
    name: str
    gp_mae: float
    gp_spearman_rho: float | None
    points_mae: float
    points_spearman_rho: float | None
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class GPBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    evaluated_players: int
    strategies: tuple[GPStrategyResult, ...]


def _weighted_games(history_games: tuple[float, ...]) -> float:
    if len(history_games) != len(DEFAULT_SEASON_WEIGHTS):
        raise ProjectionError(
            f"GP baseline shootout requires exactly {len(DEFAULT_SEASON_WEIGHTS)} history seasons"
        )
    return sum(
        games * weight
        for games, weight in zip(history_games, DEFAULT_SEASON_WEIGHTS, strict=True)
    ) / sum(DEFAULT_SEASON_WEIGHTS)


def _predict_games(strategy: str, history_games: tuple[float, ...]) -> float:
    weighted = _weighted_games(history_games)
    if strategy == "weighted_60_30_10":
        value = weighted
    elif strategy == "latest":
        value = history_games[0]
    elif strategy == "mean":
        value = sum(history_games) / len(history_games)
    elif strategy == "median":
        value = float(median(history_games))
    elif strategy == "max":
        value = max(history_games)
    elif strategy == "fixed_82":
        value = float(STANDARD_NHL_GAMES)
    elif strategy == "shrink25_to_82":
        value = 0.75 * weighted + 0.25 * STANDARD_NHL_GAMES
    elif strategy == "shrink50_to_82":
        value = 0.50 * weighted + 0.50 * STANDARD_NHL_GAMES
    elif strategy == "shrink75_to_82":
        value = 0.25 * weighted + 0.75 * STANDARD_NHL_GAMES
    else:
        raise ProjectionError(f"Unknown GP strategy: {strategy}")
    return min(float(STANDARD_NHL_GAMES), max(0.0, value))


def _top_k_overlaps(
    projected_order: list[GPBacktestPlayer],
    actual_order: list[GPBacktestPlayer],
) -> tuple[TopKOverlap, ...]:
    result: list[TopKOverlap] = []
    for requested_k in TOP_K_CUTOFFS:
        compared_k = min(requested_k, len(projected_order))
        projected_ids = {player.player_id for player in projected_order[:compared_k]}
        actual_ids = {player.player_id for player in actual_order[:compared_k]}
        overlap = len(projected_ids & actual_ids)
        result.append(
            TopKOverlap(
                requested_k=requested_k,
                compared_k=compared_k,
                overlap=overlap,
                overlap_rate=overlap / compared_k,
            )
        )
    return tuple(result)


def build_gp_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[GPBacktestPlayer, ...],
) -> GPBacktestResult:
    if not players:
        raise ProjectionError("GP baseline shootout requires at least one player")

    strategies = (
        "weighted_60_30_10",
        "latest",
        "mean",
        "median",
        "max",
        "fixed_82",
        "shrink25_to_82",
        "shrink50_to_82",
        "shrink75_to_82",
    )
    actual_games = [player.actual_games for player in players]
    actual_points = [player.actual_points for player in players]
    actual_order = sorted(players, key=lambda player: (player.actual_points, player.player_id), reverse=True)

    results: list[GPStrategyResult] = []
    for strategy in strategies:
        projected_games = [_predict_games(strategy, player.history_games) for player in players]
        projected_points = [
            games * player.projected_points_per_game
            for games, player in zip(projected_games, players, strict=True)
        ]
        gp_mae = sum(
            abs(projected - actual)
            for projected, actual in zip(projected_games, actual_games, strict=True)
        ) / len(players)
        points_mae = sum(
            abs(projected - actual)
            for projected, actual in zip(projected_points, actual_points, strict=True)
        ) / len(players)

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
            GPStrategyResult(
                name=strategy,
                gp_mae=gp_mae,
                gp_spearman_rho=spearman_rank_correlation(projected_games, actual_games),
                points_mae=points_mae,
                points_spearman_rho=spearman_rank_correlation(projected_points, actual_points),
                top_k_points=_top_k_overlaps(projected_order, actual_order),
            )
        )

    return GPBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        evaluated_players=len(players),
        strategies=tuple(results),
    )

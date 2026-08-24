from dataclasses import dataclass
from math import sqrt

from apollo.draft.projections import MODEL_VERSION, SKATER_PROJECTION_STATS, ProjectionError

BACKTEST_STATS = (
    "gamesPlayed",
    "points",
    *SKATER_PROJECTION_STATS,
)
TOP_K_CUTOFFS = (25, 50, 100)


@dataclass(frozen=True, slots=True)
class BacktestPlayer:
    player_id: int
    player_name: str
    projected_games: float
    actual_games: float
    projected_stats: dict[str, float]
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class BacktestMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None
    oracle_gp_mae: float | None
    oracle_gp_spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class TopKOverlap:
    requested_k: int
    compared_k: int
    overlap: int
    overlap_rate: float


@dataclass(frozen=True, slots=True)
class ProjectionBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    model_version: str
    min_actual_games: int
    min_history_seasons: int
    actual_eligible_players: int
    evaluated_players: int
    coverage: float
    history_counts: tuple[tuple[int, int], ...]
    skipped_incomplete_history: int
    metrics: tuple[BacktestMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]
    oracle_gp_top_k_points: tuple[TopKOverlap, ...]


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for index in range(start, end):
            ranks[indexed[index][0]] = average_rank
        start = end
    return ranks


def spearman_rank_correlation(projected: list[float], actual: list[float]) -> float | None:
    if len(projected) != len(actual):
        raise ProjectionError("Projected and actual rank vectors must have equal length")
    if len(projected) < 2:
        return None

    projected_ranks = _average_ranks(projected)
    actual_ranks = _average_ranks(actual)
    projected_mean = sum(projected_ranks) / len(projected_ranks)
    actual_mean = sum(actual_ranks) / len(actual_ranks)

    covariance = sum(
        (projected_rank - projected_mean) * (actual_rank - actual_mean)
        for projected_rank, actual_rank in zip(projected_ranks, actual_ranks, strict=True)
    )
    projected_variance = sum((rank - projected_mean) ** 2 for rank in projected_ranks)
    actual_variance = sum((rank - actual_mean) ** 2 for rank in actual_ranks)
    denominator = sqrt(projected_variance * actual_variance)
    if denominator == 0:
        return None
    return covariance / denominator


def _stat_value(player: BacktestPlayer, stat_name: str, *, projected: bool) -> float:
    if stat_name == "gamesPlayed":
        return player.projected_games if projected else player.actual_games

    stats = player.projected_stats if projected else player.actual_stats
    if stat_name == "points":
        return stats["goals"] + stats["assists"]
    return stats[stat_name]


def _oracle_gp_stat_value(player: BacktestPlayer, stat_name: str) -> float:
    if stat_name == "gamesPlayed":
        return player.actual_games
    if player.projected_games <= 0:
        raise ProjectionError(
            f"Actual-GP oracle requires positive projected games for {player.player_name}"
        )
    projected_total = _stat_value(player, stat_name, projected=True)
    projected_rate = projected_total / player.projected_games
    return projected_rate * player.actual_games


def _top_k_overlaps(
    projected_order: list[BacktestPlayer],
    actual_order: list[BacktestPlayer],
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


def build_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[BacktestPlayer, ...],
    actual_eligible_players: int,
    min_actual_games: int,
    min_history_seasons: int,
    history_counts: tuple[tuple[int, int], ...],
    skipped_incomplete_history: int = 0,
    model_version: str = MODEL_VERSION,
) -> ProjectionBacktestResult:
    if actual_eligible_players <= 0:
        raise ProjectionError("Backtest requires at least one actual-eligible skater")
    if not players:
        raise ProjectionError("No skaters satisfied the backtest history requirements")

    metrics: list[BacktestMetric] = []
    for stat_name in BACKTEST_STATS:
        projected_values = [_stat_value(player, stat_name, projected=True) for player in players]
        actual_values = [_stat_value(player, stat_name, projected=False) for player in players]
        mae = sum(
            abs(projected - actual)
            for projected, actual in zip(projected_values, actual_values, strict=True)
        ) / len(players)

        oracle_gp_mae: float | None = None
        oracle_gp_spearman_rho: float | None = None
        if stat_name != "gamesPlayed":
            oracle_values = [_oracle_gp_stat_value(player, stat_name) for player in players]
            oracle_gp_mae = sum(
                abs(projected - actual)
                for projected, actual in zip(oracle_values, actual_values, strict=True)
            ) / len(players)
            oracle_gp_spearman_rho = spearman_rank_correlation(oracle_values, actual_values)

        metrics.append(
            BacktestMetric(
                stat_name=stat_name,
                mae=mae,
                spearman_rho=spearman_rank_correlation(projected_values, actual_values),
                oracle_gp_mae=oracle_gp_mae,
                oracle_gp_spearman_rho=oracle_gp_spearman_rho,
            )
        )

    actual_order = sorted(
        players,
        key=lambda player: (_stat_value(player, "points", projected=False), player.player_id),
        reverse=True,
    )
    projected_order = sorted(
        players,
        key=lambda player: (_stat_value(player, "points", projected=True), player.player_id),
        reverse=True,
    )
    oracle_gp_order = sorted(
        players,
        key=lambda player: (_oracle_gp_stat_value(player, "points"), player.player_id),
        reverse=True,
    )

    return ProjectionBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        model_version=model_version,
        min_actual_games=min_actual_games,
        min_history_seasons=min_history_seasons,
        actual_eligible_players=actual_eligible_players,
        evaluated_players=len(players),
        coverage=len(players) / actual_eligible_players,
        history_counts=history_counts,
        skipped_incomplete_history=skipped_incomplete_history,
        metrics=tuple(metrics),
        top_k_points=_top_k_overlaps(projected_order, actual_order),
        oracle_gp_top_k_points=_top_k_overlaps(oracle_gp_order, actual_order),
    )

from dataclasses import dataclass
from statistics import median

from apollo.draft.aging import adjust_rate_for_seasons
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, SKATER_PROJECTION_STATS, ProjectionError

DEPLOYMENT_STRATEGIES = (
    "baseline_v03",
    "toi_latest",
    "toi_weighted",
    "toi_mean",
    "toi_median",
    "toi_max",
    "actual_toi_oracle",
)
DEPLOYMENT_STATS = ("points", *SKATER_PROJECTION_STATS)


@dataclass(frozen=True, slots=True)
class DeploymentHistorySeason:
    season: int
    games_played: float
    time_on_ice_per_game: float
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class DeploymentBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    target_season: int
    birth_date: object | None
    projected_games: float
    baseline_stats: dict[str, float]
    history: tuple[DeploymentHistorySeason, ...]
    actual_time_on_ice_per_game: float
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class DeploymentMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class DeploymentStrategyResult:
    strategy_name: str
    projected_toi_mae: float | None
    projected_toi_rho: float | None
    metrics: tuple[DeploymentMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class DeploymentBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    base_eligible_players: int
    evaluated_players: int
    toi_coverage: float
    strategies: tuple[DeploymentStrategyResult, ...]


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("Deployment backtest requires positive history weights")
    return sum(value * weight for value, weight in values) / weight_sum


def _project_toi(
    player: DeploymentBacktestPlayer,
    strategy_name: str,
    season_weights: tuple[float, ...],
) -> float:
    usable = [season for season in player.history if season.time_on_ice_per_game > 0]
    if not usable:
        raise ProjectionError(f"Deployment backtest has no TOI history for {player.player_name}")
    values = [season.time_on_ice_per_game for season in usable]

    if strategy_name == "actual_toi_oracle":
        return player.actual_time_on_ice_per_game
    if strategy_name == "toi_latest":
        return values[0]
    if strategy_name == "toi_mean":
        return sum(values) / len(values)
    if strategy_name == "toi_median":
        return float(median(values))
    if strategy_name == "toi_max":
        return max(values)
    if strategy_name == "toi_weighted":
        weighted = [
            (season.time_on_ice_per_game, season_weights[index])
            for index, season in enumerate(player.history)
            if index < len(season_weights) and season.time_on_ice_per_game > 0
        ]
        return _weighted_average(weighted)
    raise ProjectionError(f"Unknown deployment strategy: {strategy_name}")


def _project_stat_with_toi(
    player: DeploymentBacktestPlayer,
    stat_name: str,
    projected_toi: float,
    season_weights: tuple[float, ...],
) -> float:
    efficiency_values: list[tuple[float, float]] = []
    for index, season in enumerate(player.history):
        if (
            index >= len(season_weights)
            or season.games_played <= 0
            or season.time_on_ice_per_game <= 0
        ):
            continue
        value = season.stats.get(stat_name)
        if value is None:
            continue
        rate_per_game = value / season.games_played
        if player.birth_date is not None:
            rate_per_game = adjust_rate_for_seasons(
                observed_rate=rate_per_game,
                birth_date=player.birth_date,
                source_season=season.season,
                target_season=player.target_season,
                position=player.position,
            )
        efficiency_values.append(
            (
                rate_per_game / season.time_on_ice_per_game,
                season_weights[index],
            )
        )
    if not efficiency_values:
        raise ProjectionError(
            f"Deployment backtest has no usable '{stat_name}' history for {player.player_name}"
        )
    efficiency = _weighted_average(efficiency_values)
    return efficiency * projected_toi * player.projected_games


def _top_k_overlaps(
    player_ids: list[int],
    projected_points: list[float],
    actual_points: list[float],
) -> tuple[TopKOverlap, ...]:
    projected = sorted(
        zip(player_ids, projected_points, strict=True),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    actual = sorted(
        zip(player_ids, actual_points, strict=True),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    overlaps: list[TopKOverlap] = []
    for requested_k in TOP_K_CUTOFFS:
        compared_k = min(requested_k, len(player_ids))
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


def build_deployment_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[DeploymentBacktestPlayer, ...],
    base_eligible_players: int,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> DeploymentBacktestResult:
    if not players:
        raise ProjectionError("Deployment backtest requires at least one player with TOI history")
    if base_eligible_players < len(players):
        raise ProjectionError("base_eligible_players cannot be smaller than evaluated players")

    actual_by_stat = {
        stat: [
            (
                player.actual_stats["goals"] + player.actual_stats["assists"]
                if stat == "points"
                else player.actual_stats[stat]
            )
            for player in players
        ]
        for stat in DEPLOYMENT_STATS
    }
    actual_toi = [player.actual_time_on_ice_per_game for player in players]
    player_ids = [player.player_id for player in players]
    results: list[DeploymentStrategyResult] = []

    for strategy_name in DEPLOYMENT_STRATEGIES:
        projected_by_stat: dict[str, list[float]] = {stat: [] for stat in DEPLOYMENT_STATS}
        projected_toi_values: list[float] = []

        for player in players:
            if strategy_name == "baseline_v03":
                projected_raw = {
                    stat: player.baseline_stats[stat] for stat in SKATER_PROJECTION_STATS
                }
                projected_toi = None
            else:
                projected_toi = _project_toi(player, strategy_name, season_weights)
                projected_toi_values.append(projected_toi)
                projected_raw = {
                    stat: _project_stat_with_toi(
                        player,
                        stat,
                        projected_toi,
                        season_weights,
                    )
                    for stat in SKATER_PROJECTION_STATS
                }

            for stat in SKATER_PROJECTION_STATS:
                projected_by_stat[stat].append(projected_raw[stat])
            projected_by_stat["points"].append(
                projected_raw["goals"] + projected_raw["assists"]
            )

        metrics: list[DeploymentMetric] = []
        for stat_name in DEPLOYMENT_STATS:
            projected = projected_by_stat[stat_name]
            actual = actual_by_stat[stat_name]
            mae = sum(
                abs(projected_value - actual_value)
                for projected_value, actual_value in zip(projected, actual, strict=True)
            ) / len(players)
            metrics.append(
                DeploymentMetric(
                    stat_name=stat_name,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected, actual),
                )
            )

        toi_mae = None
        toi_rho = None
        if strategy_name != "baseline_v03":
            toi_mae = sum(
                abs(projected - actual)
                for projected, actual in zip(projected_toi_values, actual_toi, strict=True)
            ) / len(players)
            toi_rho = spearman_rank_correlation(projected_toi_values, actual_toi)

        results.append(
            DeploymentStrategyResult(
                strategy_name=strategy_name,
                projected_toi_mae=toi_mae,
                projected_toi_rho=toi_rho,
                metrics=tuple(metrics),
                top_k_points=_top_k_overlaps(
                    player_ids,
                    projected_by_stat["points"],
                    actual_by_stat["points"],
                ),
            )
        )

    return DeploymentBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        base_eligible_players=base_eligible_players,
        evaluated_players=len(players),
        toi_coverage=len(players) / base_eligible_players,
        strategies=tuple(results),
    )

from dataclasses import dataclass
from datetime import date

from apollo.draft.aging import adjust_rate_for_seasons
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError

PP_BLEND_WEIGHTS = {
    "pp_blend_weighted25": 0.25,
    "pp_blend_weighted50": 0.50,
    "pp_blend_weighted75": 0.75,
}
PP_USAGE_STRATEGIES = (
    "baseline_v03",
    "pp_toi_latest",
    "pp_toi_weighted",
    "pp_toi_mean",
    *PP_BLEND_WEIGHTS,
    "actual_pp_toi_oracle",
)


@dataclass(frozen=True, slots=True)
class PPUsageHistorySeason:
    season: int
    games_played: float
    pp_time_on_ice_per_game: float
    power_play_points: float


@dataclass(frozen=True, slots=True)
class PPUsageBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    target_season: int
    birth_date: date | None
    projected_games: float
    baseline_power_play_points: float
    history: tuple[PPUsageHistorySeason, ...]
    actual_pp_time_on_ice_per_game: float
    actual_power_play_points: float


@dataclass(frozen=True, slots=True)
class PPUsageStrategyResult:
    strategy_name: str
    pp_toi_mae: float | None
    pp_toi_spearman_rho: float | None
    ppp_mae: float
    ppp_spearman_rho: float | None
    top_k_ppp: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class PPUsageBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    base_eligible_players: int
    evaluated_players: int
    pp_history_coverage: float
    strategies: tuple[PPUsageStrategyResult, ...]


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("PP usage backtest requires positive history weights")
    return sum(value * weight for value, weight in values) / weight_sum


def _project_pp_toi(
    player: PPUsageBacktestPlayer,
    strategy_name: str,
    season_weights: tuple[float, ...],
) -> float:
    values = [season.pp_time_on_ice_per_game for season in player.history]
    if strategy_name == "actual_pp_toi_oracle":
        return player.actual_pp_time_on_ice_per_game
    if strategy_name == "pp_toi_latest":
        return values[0]
    if strategy_name == "pp_toi_mean":
        return sum(values) / len(values)
    if strategy_name == "pp_toi_weighted" or strategy_name in PP_BLEND_WEIGHTS:
        return _weighted_average(
            [
                (season.pp_time_on_ice_per_game, season_weights[index])
                for index, season in enumerate(player.history)
                if index < len(season_weights)
            ]
        )
    raise ProjectionError(f"Unknown PP usage strategy: {strategy_name}")


def _project_ppp(
    player: PPUsageBacktestPlayer,
    projected_pp_toi: float,
    season_weights: tuple[float, ...],
) -> float:
    efficiency_values: list[tuple[float, float]] = []
    for index, season in enumerate(player.history):
        if index >= len(season_weights):
            continue
        if season.games_played <= 0 or season.pp_time_on_ice_per_game <= 0:
            continue
        rate_per_game = season.power_play_points / season.games_played
        if player.birth_date is not None:
            rate_per_game = adjust_rate_for_seasons(
                observed_rate=rate_per_game,
                birth_date=player.birth_date,
                source_season=season.season,
                target_season=player.target_season,
                position=player.position,
            )
        efficiency_values.append(
            (rate_per_game / season.pp_time_on_ice_per_game, season_weights[index])
        )
    if not efficiency_values:
        raise ProjectionError(f"PP usage backtest has no usable PP history for {player.player_name}")
    efficiency = _weighted_average(efficiency_values)
    return efficiency * projected_pp_toi * player.projected_games


def _top_k_overlaps(
    player_ids: list[int],
    projected_ppp: list[float],
    actual_ppp: list[float],
) -> tuple[TopKOverlap, ...]:
    projected = sorted(
        zip(player_ids, projected_ppp, strict=True),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    actual = sorted(
        zip(player_ids, actual_ppp, strict=True),
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


def build_pp_usage_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[PPUsageBacktestPlayer, ...],
    base_eligible_players: int,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> PPUsageBacktestResult:
    if not players:
        raise ProjectionError("PP usage backtest requires at least one player with PP TOI history")
    if base_eligible_players < len(players):
        raise ProjectionError("base_eligible_players cannot be smaller than evaluated players")

    actual_ppp = [player.actual_power_play_points for player in players]
    actual_pp_toi = [player.actual_pp_time_on_ice_per_game for player in players]
    player_ids = [player.player_id for player in players]
    results: list[PPUsageStrategyResult] = []

    for strategy_name in PP_USAGE_STRATEGIES:
        projected_ppp: list[float] = []
        projected_pp_toi_values: list[float] = []
        for player in players:
            if strategy_name == "baseline_v03":
                projected_ppp.append(player.baseline_power_play_points)
                continue
            projected_pp_toi = _project_pp_toi(player, strategy_name, season_weights)
            projected_pp_toi_values.append(projected_pp_toi)
            deployment_ppp = _project_ppp(player, projected_pp_toi, season_weights)
            if strategy_name in PP_BLEND_WEIGHTS:
                deployment_weight = PP_BLEND_WEIGHTS[strategy_name]
                projected_ppp.append(
                    player.baseline_power_play_points * (1.0 - deployment_weight)
                    + deployment_ppp * deployment_weight
                )
            else:
                projected_ppp.append(deployment_ppp)

        pp_toi_mae = None
        pp_toi_rho = None
        if strategy_name != "baseline_v03":
            pp_toi_mae = sum(
                abs(projected - actual)
                for projected, actual in zip(
                    projected_pp_toi_values,
                    actual_pp_toi,
                    strict=True,
                )
            ) / len(players)
            pp_toi_rho = spearman_rank_correlation(projected_pp_toi_values, actual_pp_toi)

        ppp_mae = sum(
            abs(projected - actual)
            for projected, actual in zip(projected_ppp, actual_ppp, strict=True)
        ) / len(players)
        results.append(
            PPUsageStrategyResult(
                strategy_name=strategy_name,
                pp_toi_mae=pp_toi_mae,
                pp_toi_spearman_rho=pp_toi_rho,
                ppp_mae=ppp_mae,
                ppp_spearman_rho=spearman_rank_correlation(projected_ppp, actual_ppp),
                top_k_ppp=_top_k_overlaps(player_ids, projected_ppp, actual_ppp),
            )
        )

    return PPUsageBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        base_eligible_players=base_eligible_players,
        evaluated_players=len(players),
        pp_history_coverage=len(players) / base_eligible_players,
        strategies=tuple(results),
    )

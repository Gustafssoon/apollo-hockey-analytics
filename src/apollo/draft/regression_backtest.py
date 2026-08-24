from dataclasses import dataclass
from datetime import date

from apollo.draft.aging import adjust_rate_for_seasons
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, SKATER_PROJECTION_STATS, ProjectionError

REGRESSION_PSEUDO_GAMES = (5.0, 10.0, 20.0, 40.0)
REGRESSION_STRATEGIES = (
    "baseline_v03",
    *(f"regress_pos_{int(games)}" for games in REGRESSION_PSEUDO_GAMES),
)
REGRESSION_STATS = ("points", *SKATER_PROJECTION_STATS)


@dataclass(frozen=True, slots=True)
class RegressionHistorySeason:
    season: int
    games_played: float
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class RegressionBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    target_season: int
    birth_date: date | None
    projected_games: float
    baseline_stats: dict[str, float]
    history: tuple[RegressionHistorySeason, ...]
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class RegressionMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class RegressionStrategyResult:
    strategy_name: str
    pseudo_games: float | None
    metrics: tuple[RegressionMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class RegressionBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    evaluated_players: int
    priors: dict[tuple[int, str, str], float]
    strategies: tuple[RegressionStrategyResult, ...]


def position_group(position: str) -> str:
    return "D" if position.strip().upper().startswith("D") else "F"


def build_position_priors(
    rows: tuple[tuple[int, str, float, dict[str, float]], ...],
) -> dict[tuple[int, str, str], float]:
    totals: dict[tuple[int, str, str], float] = {}
    games: dict[tuple[int, str, str], float] = {}
    for season, position, games_played, stats in rows:
        if games_played <= 0:
            continue
        group = position_group(position)
        for stat_name in SKATER_PROJECTION_STATS:
            value = stats.get(stat_name)
            if value is None:
                continue
            key = (season, group, stat_name)
            totals[key] = totals.get(key, 0.0) + value
            games[key] = games.get(key, 0.0) + games_played
    priors = {
        key: totals[key] / games[key]
        for key in totals
        if games.get(key, 0.0) > 0
    }
    if not priors:
        raise ProjectionError("Regression backtest could not build source-season priors")
    return priors


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("Regression backtest requires positive history weights")
    return sum(value * weight for value, weight in values) / weight_sum


def _project_stat(
    player: RegressionBacktestPlayer,
    stat_name: str,
    pseudo_games: float,
    priors: dict[tuple[int, str, str], float],
    season_weights: tuple[float, ...],
) -> float:
    rates: list[tuple[float, float]] = []
    group = position_group(player.position)
    for index, season in enumerate(player.history):
        if index >= len(season_weights) or season.games_played <= 0:
            continue
        value = season.stats.get(stat_name)
        if value is None:
            continue
        prior_rate = priors.get((season.season, group, stat_name))
        if prior_rate is None:
            raise ProjectionError(
                f"Regression backtest missing {group} prior for {stat_name} in {season.season}"
            )
        rate = (value + prior_rate * pseudo_games) / (season.games_played + pseudo_games)
        if player.birth_date is not None:
            rate = adjust_rate_for_seasons(
                observed_rate=rate,
                birth_date=player.birth_date,
                source_season=season.season,
                target_season=player.target_season,
                position=player.position,
            )
        rates.append((rate, season_weights[index]))
    if not rates:
        raise ProjectionError(
            f"Regression backtest has no usable '{stat_name}' history for {player.player_name}"
        )
    return _weighted_average(rates) * player.projected_games


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


def build_regression_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[RegressionBacktestPlayer, ...],
    priors: dict[tuple[int, str, str], float],
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> RegressionBacktestResult:
    if not players:
        raise ProjectionError("Regression backtest requires at least one player")

    actual_by_stat = {
        stat_name: [
            (
                player.actual_stats["goals"] + player.actual_stats["assists"]
                if stat_name == "points"
                else player.actual_stats[stat_name]
            )
            for player in players
        ]
        for stat_name in REGRESSION_STATS
    }
    player_ids = [player.player_id for player in players]
    results: list[RegressionStrategyResult] = []

    candidates: tuple[tuple[str, float | None], ...] = (
        ("baseline_v03", None),
        *((f"regress_pos_{int(games)}", games) for games in REGRESSION_PSEUDO_GAMES),
    )

    for strategy_name, pseudo_games in candidates:
        projected_by_stat: dict[str, list[float]] = {stat: [] for stat in REGRESSION_STATS}
        for player in players:
            if pseudo_games is None:
                raw = {stat: player.baseline_stats[stat] for stat in SKATER_PROJECTION_STATS}
            else:
                raw = {
                    stat: _project_stat(
                        player,
                        stat,
                        pseudo_games,
                        priors,
                        season_weights,
                    )
                    for stat in SKATER_PROJECTION_STATS
                }
            for stat_name in SKATER_PROJECTION_STATS:
                projected_by_stat[stat_name].append(raw[stat_name])
            projected_by_stat["points"].append(raw["goals"] + raw["assists"])

        metrics: list[RegressionMetric] = []
        for stat_name in REGRESSION_STATS:
            projected = projected_by_stat[stat_name]
            actual = actual_by_stat[stat_name]
            mae = sum(
                abs(projected_value - actual_value)
                for projected_value, actual_value in zip(projected, actual, strict=True)
            ) / len(players)
            metrics.append(
                RegressionMetric(
                    stat_name=stat_name,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected, actual),
                )
            )

        results.append(
            RegressionStrategyResult(
                strategy_name=strategy_name,
                pseudo_games=pseudo_games,
                metrics=tuple(metrics),
                top_k_points=_top_k_overlaps(
                    player_ids,
                    projected_by_stat["points"],
                    actual_by_stat["points"],
                ),
            )
        )

    return RegressionBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        evaluated_players=len(players),
        priors=priors,
        strategies=tuple(results),
    )

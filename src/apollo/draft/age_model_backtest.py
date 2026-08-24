from dataclasses import dataclass

from apollo.draft.age_backtest import AGE_CURVE_STRATEGIES, age_adjusted_rate
from apollo.draft.backtest import TOP_K_CUTOFFS, TopKOverlap, spearman_rank_correlation
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, SKATER_PROJECTION_STATS, ProjectionError


AGE_MODEL_CANDIDATES: dict[str, dict[str, str]] = {
    "neutral": {stat: "neutral" for stat in SKATER_PROJECTION_STATS},
    "asymmetric_all": {stat: "asymmetric" for stat in SKATER_PROJECTION_STATS},
    "medium_all": {stat: "medium" for stat in SKATER_PROJECTION_STATS},
    "hybrid": {
        "goals": "asymmetric",
        "assists": "medium",
        "powerPlayPoints": "asymmetric",
        "shots": "asymmetric",
        "hits": "asymmetric",
        "blockedShots": "asymmetric",
    },
}


@dataclass(frozen=True, slots=True)
class AgeModelHistorySeason:
    source_age: float
    games_played: float
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class AgeModelBacktestPlayer:
    player_id: int
    player_name: str
    position: str
    projected_games: float
    target_age: float
    history: tuple[AgeModelHistorySeason, ...]
    actual_stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class AgeModelMetric:
    stat_name: str
    mae: float
    spearman_rho: float | None


@dataclass(frozen=True, slots=True)
class AgeModelCandidateResult:
    candidate_name: str
    metrics: tuple[AgeModelMetric, ...]
    top_k_points: tuple[TopKOverlap, ...]


@dataclass(frozen=True, slots=True)
class AgeModelBacktestResult:
    target_season: int
    source_seasons: tuple[int, ...]
    evaluated_players: int
    candidates: tuple[AgeModelCandidateResult, ...]


def _curve(name: str):
    return next(strategy for strategy in AGE_CURVE_STRATEGIES if strategy.name == name)


def _project_stat(
    player: AgeModelBacktestPlayer,
    stat_name: str,
    strategy_name: str,
) -> float:
    strategy = _curve(strategy_name)
    weighted_rates: list[tuple[float, float]] = []
    for index, season in enumerate(player.history):
        if season.games_played <= 0 or index >= len(DEFAULT_SEASON_WEIGHTS):
            continue
        observed_rate = season.stats[stat_name] / season.games_played
        weighted_rates.append(
            (
                age_adjusted_rate(
                    observed_rate=observed_rate,
                    source_age=season.source_age,
                    target_age=player.target_age,
                    position=player.position,
                    strategy=strategy,
                ),
                DEFAULT_SEASON_WEIGHTS[index],
            )
        )
    weight_sum = sum(weight for _, weight in weighted_rates)
    if weight_sum <= 0:
        raise ProjectionError(f"Age model shootout has no usable history for {player.player_name}")
    projected_rate = sum(rate * weight for rate, weight in weighted_rates) / weight_sum
    return projected_rate * player.projected_games


def _top_k_overlaps(
    projected_points: dict[int, float],
    actual_points: dict[int, float],
) -> tuple[TopKOverlap, ...]:
    projected_order = sorted(projected_points, key=lambda pid: (projected_points[pid], pid), reverse=True)
    actual_order = sorted(actual_points, key=lambda pid: (actual_points[pid], pid), reverse=True)
    overlaps: list[TopKOverlap] = []
    for requested_k in TOP_K_CUTOFFS:
        compared_k = min(requested_k, len(projected_order))
        overlap = len(set(projected_order[:compared_k]) & set(actual_order[:compared_k]))
        overlaps.append(
            TopKOverlap(
                requested_k=requested_k,
                compared_k=compared_k,
                overlap=overlap,
                overlap_rate=overlap / compared_k,
            )
        )
    return tuple(overlaps)


def build_age_model_backtest_result(
    *,
    target_season: int,
    source_seasons: tuple[int, ...],
    players: tuple[AgeModelBacktestPlayer, ...],
) -> AgeModelBacktestResult:
    if not players:
        raise ProjectionError("Age model shootout requires at least one player")

    actual_by_stat = {
        stat: [player.actual_stats[stat] for player in players]
        for stat in SKATER_PROJECTION_STATS
    }
    actual_points = {
        player.player_id: player.actual_stats["goals"] + player.actual_stats["assists"]
        for player in players
    }
    candidate_results: list[AgeModelCandidateResult] = []

    for candidate_name, strategy_map in AGE_MODEL_CANDIDATES.items():
        projected_by_stat: dict[str, list[float]] = {stat: [] for stat in SKATER_PROJECTION_STATS}
        projected_points_by_id: dict[int, float] = {}

        for player in players:
            projected_stats = {
                stat: _project_stat(player, stat, strategy_map[stat])
                for stat in SKATER_PROJECTION_STATS
            }
            for stat in SKATER_PROJECTION_STATS:
                projected_by_stat[stat].append(projected_stats[stat])
            projected_points_by_id[player.player_id] = (
                projected_stats["goals"] + projected_stats["assists"]
            )

        metrics: list[AgeModelMetric] = []
        projected_points = [projected_points_by_id[player.player_id] for player in players]
        actual_points_list = [actual_points[player.player_id] for player in players]
        points_mae = sum(
            abs(projected - actual)
            for projected, actual in zip(projected_points, actual_points_list, strict=True)
        ) / len(players)
        metrics.append(
            AgeModelMetric(
                stat_name="points",
                mae=points_mae,
                spearman_rho=spearman_rank_correlation(projected_points, actual_points_list),
            )
        )
        for stat in SKATER_PROJECTION_STATS:
            projected_values = projected_by_stat[stat]
            actual_values = actual_by_stat[stat]
            mae = sum(
                abs(projected - actual)
                for projected, actual in zip(projected_values, actual_values, strict=True)
            ) / len(players)
            metrics.append(
                AgeModelMetric(
                    stat_name=stat,
                    mae=mae,
                    spearman_rho=spearman_rank_correlation(projected_values, actual_values),
                )
            )

        candidate_results.append(
            AgeModelCandidateResult(
                candidate_name=candidate_name,
                metrics=tuple(metrics),
                top_k_points=_top_k_overlaps(projected_points_by_id, actual_points),
            )
        )

    return AgeModelBacktestResult(
        target_season=target_season,
        source_seasons=source_seasons,
        evaluated_players=len(players),
        candidates=tuple(candidate_results),
    )

from dataclasses import dataclass

from apollo.draft.goalie_baseline import (
    GOALIE_TOTAL_STATS,
    GoalieBacktestMetric,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
)
from apollo.draft.projections import ProjectionError

GOALIE_WORKLOAD_VARIANTS = (
    ("share-603010", (0.60, 0.30, 0.10)),
    ("share-702010", (0.70, 0.20, 0.10)),
    ("share-801505", (0.80, 0.15, 0.05)),
)
TARGET_TEAM_GAMES = 82.0
GOALIE_WORKLOAD_STATS = (
    "gamesStarted",
    "wins",
    "saves",
    "goalsAgainst",
    "shutouts",
    "savePctg",
    "goalsAgainstAvg",
)


def scheduled_team_games(season: int) -> float:
    if season == 20202021:
        return 56.0
    if season >= 20212022:
        return 82.0
    raise ProjectionError(f"Goalie workload schedule is undefined for season {season}")


def project_workload_starts(
    history: tuple[tuple[int, dict[str, float]], ...],
    weights: tuple[float, float, float],
) -> float:
    if len(history) != 3 or len(weights) != 3:
        raise ProjectionError("Goalie workload candidate requires exactly three source seasons")
    shares = []
    for season, stats in history:
        starts = stats.get("gamesStarted", 0.0)
        if starts <= 0:
            raise ProjectionError("Goalie workload candidate requires positive source starts")
        shares.append(starts / scheduled_team_games(season))
    weighted_share = sum(
        share * weight for share, weight in zip(shares, weights, strict=True)
    )
    return min(TARGET_TEAM_GAMES, max(0.0, weighted_share * TARGET_TEAM_GAMES))


def apply_workload_to_baseline(
    baseline: GoalieBacktestPlayer,
    projected_starts: float,
) -> GoalieBacktestPlayer:
    if baseline.projected_starts <= 0:
        raise ProjectionError("Goalie workload candidate requires positive baseline starts")
    scale = projected_starts / baseline.projected_starts
    stats = dict(baseline.projected_stats)
    for stat_name in GOALIE_TOTAL_STATS:
        stats[stat_name] *= scale
    return GoalieBacktestPlayer(
        player_id=baseline.player_id,
        player_name=baseline.player_name,
        projected_starts=projected_starts,
        actual_starts=baseline.actual_starts,
        projected_stats=stats,
        actual_stats=baseline.actual_stats,
    )


@dataclass(frozen=True, slots=True)
class GoalieWorkloadVariantSeasonResult:
    name: str
    result: GoalieBacktestResult


@dataclass(frozen=True, slots=True)
class GoalieWorkloadSeasonResult:
    target_season: int
    baseline: GoalieBacktestResult
    variants: tuple[GoalieWorkloadVariantSeasonResult, ...]


@dataclass(frozen=True, slots=True)
class GoalieWorkloadVariantAggregate:
    name: str
    player_seasons: int
    metrics: tuple[GoalieBacktestMetric, ...]
    improved_years: int
    worst_gs_mae_gain: float


@dataclass(frozen=True, slots=True)
class GoalieWorkloadAggregate:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    variants: tuple[GoalieWorkloadVariantAggregate, ...]


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def build_goalie_workload_aggregate(
    results: tuple[GoalieWorkloadSeasonResult, ...],
) -> GoalieWorkloadAggregate:
    if not results:
        raise ProjectionError("Goalie workload aggregate requires season results")
    total_n = sum(item.baseline.evaluated_goalies for item in results)
    variants = []
    for name, _ in GOALIE_WORKLOAD_VARIANTS:
        season_variants = [
            next(variant for variant in item.variants if variant.name == name)
            for item in results
        ]
        metrics = []
        for stat_name in GOALIE_WORKLOAD_STATS:
            pairs = [
                (_metric(variant.result, stat_name), item.baseline.evaluated_goalies)
                for variant, item in zip(season_variants, results, strict=True)
            ]
            mae = sum(metric.mae * n for metric, n in pairs) / total_n
            rho_pairs = [
                (metric.spearman_rho, n)
                for metric, n in pairs
                if metric.spearman_rho is not None
            ]
            rho = (
                None
                if not rho_pairs
                else sum(float(value) * n for value, n in rho_pairs)
                / sum(n for _, n in rho_pairs)
            )
            metrics.append(GoalieBacktestMetric(stat_name, mae, rho, None, None))
        gs_gains = [
            _metric(item.baseline, "gamesStarted").mae
            - _metric(variant.result, "gamesStarted").mae
            for item, variant in zip(results, season_variants, strict=True)
        ]
        variants.append(
            GoalieWorkloadVariantAggregate(
                name=name,
                player_seasons=total_n,
                metrics=tuple(metrics),
                improved_years=sum(gain > 0 for gain in gs_gains),
                worst_gs_mae_gain=min(gs_gains),
            )
        )
    return GoalieWorkloadAggregate(
        target_seasons=tuple(item.target_season for item in results),
        baseline_player_seasons=total_n,
        variants=tuple(variants),
    )

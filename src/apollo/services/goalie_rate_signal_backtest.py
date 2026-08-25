from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    build_goalie_projection,
)
from apollo.draft.goalie_rate_signal_backtest import (
    GOALIE_RATE_SIGNALS,
    GoalieRateSignalAggregate,
    GoalieRateSignalSeasonMetric,
    build_signal_aggregate,
    build_signal_metric,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def run_goalie_rate_signal_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> tuple[int, tuple[GoalieRateSignalSeasonMetric, ...]]:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT p.id AS player_id, ns.season, ns.stat_name, ns.value
            FROM player p
            JOIN player_external_id nhl
              ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        stats_by_player[int(row["player_id"])][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    pairs = {signal_name: [] for signal_name in GOALIE_RATE_SIGNALS}
    baseline_player_seasons = 0
    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)

    for seasons_by_stat in stats_by_player.values():
        actual = seasons_by_stat.get(target_season, {})
        if actual.get("gamesStarted", 0.0) < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue

        history: list[tuple[int, dict[str, float]]] = []
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            if stats.get("gamesStarted", 0.0) <= 0:
                break
            if any(stat_name not in stats for stat_name in source_required):
                break
            history.append((season, stats))
        if len(history) != 3:
            continue

        projection = build_goalie_projection(tuple(history))
        baseline_player_seasons += 1
        save_pct_residual = actual["savePctg"] - projection.stats["savePctg"]
        gaa_residual = actual["goalsAgainstAvg"] - projection.stats["goalsAgainstAvg"]
        pairs["weighted_save_pct"].append((projection.stats["savePctg"], save_pct_residual))
        pairs["latest_save_pct"].append((history[0][1]["savePctg"], save_pct_residual))
        pairs["weighted_gaa"].append((projection.stats["goalsAgainstAvg"], gaa_residual))
        pairs["latest_gaa"].append((history[0][1]["goalsAgainstAvg"], gaa_residual))

    if baseline_player_seasons <= 0:
        raise ProjectionError("Goalie rate signal screen requires evaluated goalies")
    metrics = tuple(
        build_signal_metric(signal_name, target_season, tuple(pairs[signal_name]))
        for signal_name in GOALIE_RATE_SIGNALS
    )
    return baseline_player_seasons, metrics


def run_goalie_rate_signal_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieRateSignalAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    baseline_player_seasons = 0
    season_metrics: list[GoalieRateSignalSeasonMetric] = []
    for target_season in target_seasons:
        season_n, metrics = run_goalie_rate_signal_backtest(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        baseline_player_seasons += season_n
        season_metrics.extend(metrics)
    return build_signal_aggregate(
        target_seasons,
        baseline_player_seasons,
        tuple(season_metrics),
    )

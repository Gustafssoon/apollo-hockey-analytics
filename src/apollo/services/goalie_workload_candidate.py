from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.goalie_workload_candidate import (
    GOALIE_WORKLOAD_VARIANTS,
    GoalieWorkloadAggregate,
    GoalieWorkloadSeasonResult,
    GoalieWorkloadVariantSeasonResult,
    apply_workload_to_baseline,
    build_goalie_workload_aggregate,
    project_workload_starts,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def run_goalie_workload_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieWorkloadSeasonResult:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")
    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT p.id AS player_id, p.first_name, p.last_name,
                   ns.season, ns.stat_name, ns.value
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

    names: dict[int, str] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["player_id"])
        names[player_id] = f"{row['first_name']} {row['last_name']}"
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    eligible = 0
    baseline_players: list[GoalieBacktestPlayer] = []
    candidate_players = {name: [] for name, _ in GOALIE_WORKLOAD_VARIANTS}

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if actual_starts < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue
        eligible += 1

        history = []
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
        baseline = GoalieBacktestPlayer(
            player_id=player_id,
            player_name=names[player_id],
            projected_starts=projection.projected_starts,
            actual_starts=actual_starts,
            projected_stats=projection.stats,
            actual_stats=actual,
        )
        baseline_players.append(baseline)
        for name, weights in GOALIE_WORKLOAD_VARIANTS:
            starts = project_workload_starts(tuple(history), weights)
            candidate_players[name].append(apply_workload_to_baseline(baseline, starts))

    baseline_result = build_goalie_backtest_result(
        target_season=target_season,
        players=tuple(baseline_players),
        actual_eligible_goalies=eligible,
    )
    variants = tuple(
        GoalieWorkloadVariantSeasonResult(
            name=name,
            result=build_goalie_backtest_result(
                target_season=target_season,
                players=tuple(candidate_players[name]),
                actual_eligible_goalies=eligible,
            ),
        )
        for name, _ in GOALIE_WORKLOAD_VARIANTS
    )
    return GoalieWorkloadSeasonResult(
        target_season=target_season,
        baseline=baseline_result,
        variants=variants,
    )


def run_goalie_workload_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieWorkloadAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    targets = (latest_target_season, *previous_seasons(latest_target_season, years - 1))
    results = tuple(
        run_goalie_workload_candidate_backtest(
            database,
            season,
            min_actual_starts=min_actual_starts,
        )
        for season in targets
    )
    return build_goalie_workload_aggregate(results)

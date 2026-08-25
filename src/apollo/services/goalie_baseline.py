from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
    GoalieBaselineAggregate,
    build_goalie_backtest_result,
    build_goalie_baseline_aggregate,
    build_goalie_projection,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def run_goalie_baseline_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieBacktestResult:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)

    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id AS player_id,
                p.first_name,
                p.last_name,
                ns.season,
                ns.stat_name,
                ns.value
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns
                ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    player_names: dict[int, str] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["player_id"])
        player_names[player_id] = f"{row['first_name']} {row['last_name']}"
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    actual_eligible_goalies = 0
    players: list[GoalieBacktestPlayer] = []
    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if actual_starts < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue
        actual_eligible_goalies += 1

        history: list[tuple[int, dict[str, float]]] = []
        complete = True
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            if stats.get("gamesStarted", 0.0) <= 0:
                complete = False
                break
            if any(stat_name not in stats for stat_name in source_required):
                complete = False
                break
            history.append((season, stats))
        if not complete:
            continue

        projection = build_goalie_projection(tuple(history))
        players.append(
            GoalieBacktestPlayer(
                player_id=player_id,
                player_name=player_names[player_id],
                projected_starts=projection.projected_starts,
                actual_starts=actual_starts,
                projected_stats=projection.stats,
                actual_stats=actual,
            )
        )

    return build_goalie_backtest_result(
        target_season=target_season,
        players=tuple(players),
        actual_eligible_goalies=actual_eligible_goalies,
    )


def run_goalie_baseline_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieBaselineAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_goalie_baseline_backtest(
            database,
            season,
            min_actual_starts=min_actual_starts,
        )
        for season in target_seasons
    )
    return build_goalie_baseline_aggregate(results)

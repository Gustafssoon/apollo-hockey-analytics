from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.goalie_baseline_v02_candidate import (
    GoalieBaselineV02Aggregate,
    GoalieBaselineV02SeasonResult,
    build_goalie_baseline_v02_aggregate,
    build_goalie_baseline_v02_player,
)
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.services.goalie_rate_candidate import _build_source_priors


def run_goalie_baseline_v02_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieBaselineV02SeasonResult:
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

    save_pct_prior, gaa_prior = _build_source_priors(stats_by_player, source_seasons)
    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    eligible = 0
    baseline_players: list[GoalieBacktestPlayer] = []
    candidate_players: list[GoalieBacktestPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if actual_starts < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue
        eligible += 1

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
        baseline = GoalieBacktestPlayer(
            player_id=player_id,
            player_name=names[player_id],
            projected_starts=projection.projected_starts,
            actual_starts=actual_starts,
            projected_stats=projection.stats,
            actual_stats=actual,
        )
        baseline_players.append(baseline)
        candidate_players.append(
            build_goalie_baseline_v02_player(
                baseline,
                save_pct_prior=save_pct_prior,
                gaa_prior=gaa_prior,
            )
        )

    return GoalieBaselineV02SeasonResult(
        target_season=target_season,
        baseline=build_goalie_backtest_result(
            target_season=target_season,
            players=tuple(baseline_players),
            actual_eligible_goalies=eligible,
        ),
        candidate=build_goalie_backtest_result(
            target_season=target_season,
            players=tuple(candidate_players),
            actual_eligible_goalies=eligible,
        ),
        save_pct_prior=save_pct_prior,
        gaa_prior=gaa_prior,
    )


def run_goalie_baseline_v02_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieBaselineV02Aggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_goalie_baseline_v02_candidate_backtest(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        for target_season in target_seasons
    )
    return build_goalie_baseline_v02_aggregate(results)

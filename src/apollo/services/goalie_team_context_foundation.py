from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import GOALIE_BACKTEST_STATS, GOALIE_REQUIRED_SOURCE_STATS
from apollo.draft.goalie_team_context_foundation import (
    GOALIE_TEAM_DOMINANCE_THRESHOLD,
    GoalieTeamContextAggregate,
    GoalieTeamContextSeasonAudit,
    build_goalie_team_context_aggregate,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def run_goalie_team_context_audit_season(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieTeamContextSeasonAudit:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    stat_seasons = (target_season, *source_seasons)
    stat_placeholders = ", ".join("?" for _ in stat_seasons)
    source_placeholders = ", ".join("?" for _ in source_seasons)

    with database.connect() as connection:
        stat_rows = connection.execute(
            f"""
            SELECT p.id AS player_id, ns.season, ns.stat_name, ns.value
            FROM player p
            JOIN player_external_id nhl
              ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({stat_placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            ORDER BY p.id, ns.season, ns.stat_name
            """,
            stat_seasons,
        ).fetchall()
        log_rows = connection.execute(
            f"""
            SELECT
                pg.player_id,
                g.season,
                pg.team_abbrev,
                COUNT(*) AS games
            FROM nhl_player_game pg
            JOIN nhl_game g ON g.game_id = pg.game_id
            JOIN player p ON p.id = pg.player_id
            WHERE g.game_type = 2
              AND g.season IN ({source_placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            GROUP BY pg.player_id, g.season, pg.team_abbrev
            ORDER BY pg.player_id, g.season, pg.team_abbrev
            """,
            source_seasons,
        ).fetchall()

    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in stat_rows:
        stats_by_player[int(row["player_id"])][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    logs_by_player: dict[int, dict[int, dict[str | None, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in log_rows:
        team = str(row["team_abbrev"]).upper() if row["team_abbrev"] else None
        logs_by_player[int(row["player_id"])][int(row["season"])][team] = int(row["games"])

    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    baseline_goalies = 0
    with_game_logs = 0
    with_gp_stat = 0
    gp_log_match = 0
    team_identified = 0
    dominant_team_80 = 0
    multi_team = 0
    goalies_all3_team_identified = 0
    goalies_all3_dominant_80 = 0

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        if actual.get("gamesStarted", 0.0) < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue

        history = []
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            if stats.get("gamesStarted", 0.0) <= 0:
                break
            if any(stat_name not in stats for stat_name in source_required):
                break
            history.append(stats)
        if len(history) != 3:
            continue

        baseline_goalies += 1
        all_team = True
        all_dominant = True
        for season, stats in zip(source_seasons, history, strict=True):
            team_counts = logs_by_player.get(player_id, {}).get(season, {})
            total_logs = sum(team_counts.values())
            named_counts = {team: n for team, n in team_counts.items() if team is not None}
            if total_logs > 0:
                with_game_logs += 1
            gp = stats.get("gamesPlayed")
            if gp is not None:
                with_gp_stat += 1
                if total_logs > 0 and abs(total_logs - gp) <= 1.0:
                    gp_log_match += 1
            if named_counts:
                team_identified += 1
            else:
                all_team = False
                all_dominant = False
                continue
            if len(named_counts) > 1:
                multi_team += 1
            dominant_fraction = max(named_counts.values()) / total_logs if total_logs > 0 else 0.0
            if dominant_fraction >= GOALIE_TEAM_DOMINANCE_THRESHOLD:
                dominant_team_80 += 1
            else:
                all_dominant = False

        if all_team:
            goalies_all3_team_identified += 1
        if all_dominant:
            goalies_all3_dominant_80 += 1

    if baseline_goalies <= 0:
        raise ProjectionError("Goalie team-context audit requires baseline goalies")

    return GoalieTeamContextSeasonAudit(
        target_season=target_season,
        baseline_goalies=baseline_goalies,
        source_player_seasons=baseline_goalies * 3,
        with_game_logs=with_game_logs,
        with_gp_stat=with_gp_stat,
        gp_log_match=gp_log_match,
        team_identified=team_identified,
        dominant_team_80=dominant_team_80,
        multi_team=multi_team,
        goalies_all3_team_identified=goalies_all3_team_identified,
        goalies_all3_dominant_80=goalies_all3_dominant_80,
    )


def run_goalie_team_context_audit(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieTeamContextAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    seasons = tuple(
        run_goalie_team_context_audit_season(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        for target_season in target_seasons
    )
    return build_goalie_team_context_aggregate(seasons)

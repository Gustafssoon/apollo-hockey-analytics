from collections import defaultdict

from apollo.db import Database
from apollo.draft.gp_backtest import GPBacktestPlayer, GPBacktestResult, build_gp_backtest_result
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)


def run_gp_baseline_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
) -> GPBacktestResult:
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, len(DEFAULT_SEASON_WEIGHTS))
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)

    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
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
              AND UPPER(COALESCE(p.primary_position, '')) <> 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    player_meta: dict[int, tuple[str, str, str | None, str]] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["id"])
        player_meta[player_id] = (
            str(row["first_name"]),
            str(row["last_name"]),
            row["nhl_team"],
            str(row["primary_position"] or ""),
        )
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    actual_required = ("gamesPlayed", *SKATER_PROJECTION_STATS)
    players: list[GPBacktestPlayer] = []
    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        actual_games = actual_stats["gamesPlayed"]
        if actual_games < min_actual_games:
            continue

        history: list[ProjectionSeason] = []
        history_games: list[float] = []
        complete_history = True
        for season in source_seasons:
            season_stats = seasons_by_stat.get(season, {})
            games_played = season_stats.get("gamesPlayed", 0.0)
            if games_played <= 0:
                complete_history = False
                break
            history_games.append(games_played)
            history.append(
                ProjectionSeason(
                    season=season,
                    games_played=games_played,
                    stats=season_stats,
                )
            )
        if not complete_history:
            continue

        first_name, last_name, team_abbrev, position = player_meta[player_id]
        player_name = f"{first_name} {last_name}"
        try:
            projection = build_skater_projection(
                player_id=player_id,
                player_name=player_name,
                team_abbrev=team_abbrev,
                position=position,
                target_season=target_season,
                history=tuple(history),
            )
        except ProjectionError:
            continue
        if projection.projected_games <= 0:
            continue

        players.append(
            GPBacktestPlayer(
                player_id=player_id,
                player_name=player_name,
                actual_games=actual_games,
                history_games=tuple(history_games),
                projected_points_per_game=(
                    projection.stats["goals"] + projection.stats["assists"]
                )
                / projection.projected_games,
                actual_points=actual_stats["goals"] + actual_stats["assists"],
            )
        )

    return build_gp_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        players=tuple(players),
    )

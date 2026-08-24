from apollo.db import Database
from apollo.draft.projections import (
    ProjectionError,
    ProjectionSeason,
    SkaterProjection,
    build_skater_projection,
    previous_seasons,
)


def project_skater(
    database: Database,
    name: str,
    target_season: int,
) -> SkaterProjection:
    player_name = name.strip()
    if not player_name:
        raise ProjectionError("Player name must not be empty")

    source_seasons = previous_seasons(target_season)
    placeholders = ", ".join("?" for _ in source_seasons)

    with database.connect() as connection:
        players = connection.execute(
            """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            WHERE LOWER(p.first_name || ' ' || p.last_name) = LOWER(?)
            """,
            (player_name,),
        ).fetchall()

        if not players:
            raise ProjectionError(f"Player not found in Apollo NHL data: {player_name}")
        if len(players) > 1:
            raise ProjectionError(f"Player name is ambiguous in Apollo NHL data: {player_name}")

        player = players[0]
        position = str(player["primary_position"] or "")
        if position.upper() == "G":
            raise ProjectionError("Goalie projections are not implemented in baseline v0.1")

        rows = connection.execute(
            f"""
            SELECT season, stat_name, value
            FROM nhl_player_season_stat
            WHERE player_id = ?
              AND game_type = 2
              AND season IN ({placeholders})
            ORDER BY season DESC, stat_name
            """,
            (player["id"], *source_seasons),
        ).fetchall()

    by_season: dict[int, dict[str, float]] = {}
    for row in rows:
        season = int(row["season"])
        by_season.setdefault(season, {})[str(row["stat_name"])] = float(row["value"])

    history: list[ProjectionSeason] = []
    for season in source_seasons:
        stats = by_season.get(season)
        if not stats:
            continue
        games_played = stats.get("gamesPlayed")
        if games_played is None:
            continue
        history.append(
            ProjectionSeason(
                season=season,
                games_played=games_played,
                stats=stats,
            )
        )

    full_name = f"{player['first_name']} {player['last_name']}"
    return build_skater_projection(
        player_id=int(player["id"]),
        player_name=full_name,
        team_abbrev=player["nhl_team"],
        position=position,
        target_season=target_season,
        history=tuple(history),
    )

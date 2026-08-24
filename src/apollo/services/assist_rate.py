from apollo.db import Database
from apollo.draft.regression import position_group


def load_assist_rate_priors(
    database: Database,
    source_seasons: tuple[int, ...],
) -> dict[tuple[int, str], float]:
    if not source_seasons:
        return {}

    placeholders = ", ".join("?" for _ in source_seasons)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id AS player_id,
                p.primary_position,
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
              AND ns.stat_name IN ('gamesPlayed', 'timeOnIcePerGame5v5', 'assistsPer605v5')
              AND UPPER(COALESCE(p.primary_position, '')) <> 'G'
            ORDER BY p.id, ns.season, ns.stat_name
            """,
            source_seasons,
        ).fetchall()

    stats_by_player_season: dict[tuple[int, int], dict[str, float]] = {}
    positions: dict[int, str] = {}
    for row in rows:
        player_id = int(row["player_id"])
        season = int(row["season"])
        positions[player_id] = str(row["primary_position"] or "")
        stats_by_player_season.setdefault((player_id, season), {})[
            str(row["stat_name"])
        ] = float(row["value"])

    totals: dict[tuple[int, str], float] = {}
    exposures: dict[tuple[int, str], float] = {}
    for (player_id, season), stats in stats_by_player_season.items():
        games_played = stats.get("gamesPlayed", 0.0)
        toi_per_game = stats.get("timeOnIcePerGame5v5", 0.0)
        assist_rate = stats.get("assistsPer605v5")
        exposure = games_played * toi_per_game
        if exposure <= 0 or assist_rate is None or assist_rate < 0:
            continue
        key = (season, position_group(positions.get(player_id, "")))
        totals[key] = totals.get(key, 0.0) + assist_rate * exposure
        exposures[key] = exposures.get(key, 0.0) + exposure

    return {
        key: totals[key] / exposures[key]
        for key in totals
        if exposures.get(key, 0.0) > 0
    }

from apollo.db import Database
from apollo.draft.regression import position_group


def load_pp_deployment_priors(
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
                p.id,
                p.primary_position,
                ns.season,
                MAX(CASE WHEN ns.stat_name = 'gamesPlayed' THEN ns.value END) AS games_played,
                MAX(
                    CASE
                        WHEN ns.stat_name = 'powerPlayTimeOnIcePerGame' THEN ns.value
                    END
                ) AS pp_toi_per_game
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns
                ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND ns.stat_name IN ('gamesPlayed', 'powerPlayTimeOnIcePerGame')
              AND UPPER(COALESCE(p.primary_position, '')) <> 'G'
            GROUP BY p.id, p.primary_position, ns.season
            """,
            source_seasons,
        ).fetchall()

    totals: dict[tuple[int, str], float] = {}
    exposures: dict[tuple[int, str], float] = {}
    for row in rows:
        games_played = row["games_played"]
        pp_toi_per_game = row["pp_toi_per_game"]
        if games_played is None or pp_toi_per_game is None:
            continue
        games = float(games_played)
        pp_toi = float(pp_toi_per_game)
        if games <= 0 or pp_toi < 0:
            continue
        group = position_group(str(row["primary_position"] or ""))
        key = (int(row["season"]), group)
        totals[key] = totals.get(key, 0.0) + pp_toi * games
        exposures[key] = exposures.get(key, 0.0) + games

    return {
        key: totals[key] / exposures[key]
        for key in totals
        if exposures.get(key, 0.0) > 0
    }

from apollo.db import Database
from apollo.draft.regression import position_group


def load_overall_finishing_priors(
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
              AND ns.stat_name IN ('shotTypeShootingPct', 'shotTypeShots', 'shots')
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
        shooting_pct = stats.get("shotTypeShootingPct")
        exposure = stats.get("shotTypeShots", stats.get("shots", 0.0))
        if shooting_pct is None or shooting_pct < 0 or exposure <= 0:
            continue
        key = (season, position_group(positions.get(player_id, "")))
        totals[key] = totals.get(key, 0.0) + shooting_pct * exposure
        exposures[key] = exposures.get(key, 0.0) + exposure

    return {
        key: totals[key] / exposures[key]
        for key in totals
        if exposures.get(key, 0.0) > 0
    }

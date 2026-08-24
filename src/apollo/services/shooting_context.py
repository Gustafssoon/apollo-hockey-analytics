from collections import defaultdict

from apollo.db import Database
from apollo.draft.regression import position_group


def load_shooting_context_priors(
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
                ns.stat_name,
                ns.value
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns
                ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND ns.stat_name IN ('gamesPlayed', 'shootingPct5v5')
              AND UPPER(COALESCE(p.primary_position, '')) <> 'G'
            ORDER BY p.id, ns.season, ns.stat_name
            """,
            source_seasons,
        ).fetchall()

    stats_by_player_season: dict[tuple[int, int, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        key = (
            int(row["id"]),
            int(row["season"]),
            str(row["primary_position"] or ""),
        )
        stats_by_player_season[key][str(row["stat_name"])] = float(row["value"])

    totals: dict[tuple[int, str], float] = {}
    games: dict[tuple[int, str], float] = {}
    for (_, season, position), stats in stats_by_player_season.items():
        games_played = stats.get("gamesPlayed", 0.0)
        shooting_pct = stats.get("shootingPct5v5")
        if games_played <= 0 or shooting_pct is None or shooting_pct <= 0:
            continue
        key = (season, position_group(position))
        totals[key] = totals.get(key, 0.0) + shooting_pct * games_played
        games[key] = games.get(key, 0.0) + games_played

    return {
        key: totals[key] / games[key]
        for key in totals
        if games.get(key, 0.0) > 0
    }

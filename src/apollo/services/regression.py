from collections import defaultdict

from apollo.db import Database
from apollo.draft.regression import build_position_priors


def load_position_priors(
    database: Database,
    source_seasons: tuple[int, ...],
) -> dict[tuple[int, str, str], float]:
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

    prior_rows: list[tuple[int, str, float, dict[str, float]]] = []
    for (_, season, position), stats in stats_by_player_season.items():
        games_played = stats.get("gamesPlayed", 0.0)
        if games_played <= 0:
            continue
        prior_rows.append((season, position, games_played, stats))
    return build_position_priors(prior_rows)

from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_foundation import GoalieFoundationAudit, build_goalie_foundation_audit
from apollo.draft.projections import previous_seasons


def run_goalie_foundation_audit(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
) -> GoalieFoundationAudit:
    database.initialize()
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    oldest_target = target_seasons[-1]
    seasons = (
        *target_seasons,
        *tuple(
            season
            for season in previous_seasons(oldest_target, 3)
            if season not in target_seasons
        ),
    )
    placeholders = ", ".join("?" for _ in seasons)

    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id AS player_id,
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

    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        stats_by_player[int(row["player_id"])][int(row["season"])][str(row["stat_name"])] = (
            float(row["value"])
        )

    return build_goalie_foundation_audit(
        stats_by_player,
        latest_target_season,
        years=years,
    )

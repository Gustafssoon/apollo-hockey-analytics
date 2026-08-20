from dataclasses import dataclass
from typing import Protocol

from apollo.adapters.nhl_stats import NHLSeasonStatLine
from apollo.db import Database


class NHLSeasonStatsAdapter(Protocol):
    def fetch_skater_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]: ...

    def fetch_goalie_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]: ...


@dataclass(frozen=True, slots=True)
class NHLCategoryStatsSyncResult:
    skaters: int
    goalies: int
    matched: int
    unmatched: int
    stats_written: int


def sync_nhl_category_stats(
    database: Database,
    adapter: NHLSeasonStatsAdapter,
    season: int,
    game_type: int = 2,
) -> NHLCategoryStatsSyncResult:
    database.initialize()
    skaters = adapter.fetch_skater_stats(season, game_type)
    goalies = adapter.fetch_goalie_stats(season, game_type)
    lines = (*skaters, *goalies)

    matched = 0
    unmatched = 0
    stats_written = 0

    with database.connect() as connection:
        identity_rows = connection.execute(
            """
            SELECT external_id, player_id
            FROM player_external_id
            WHERE provider = 'nhl'
            """
        ).fetchall()
        identities = {
            str(row["external_id"]): int(row["player_id"])
            for row in identity_rows
        }

        for line in lines:
            player_id = identities.get(str(line.nhl_player_id))
            if player_id is None:
                unmatched += 1
                continue

            connection.executemany(
                """
                INSERT INTO nhl_player_season_stat (
                    player_id,
                    season,
                    game_type,
                    stat_name,
                    value
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(player_id, season, game_type, stat_name)
                DO UPDATE SET value = excluded.value
                """,
                [
                    (player_id, season, game_type, stat.name, stat.value)
                    for stat in line.stats
                ],
            )
            stats_written += len(line.stats)
            matched += 1

    return NHLCategoryStatsSyncResult(
        skaters=len(skaters),
        goalies=len(goalies),
        matched=matched,
        unmatched=unmatched,
        stats_written=stats_written,
    )

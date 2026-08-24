from dataclasses import dataclass
from typing import Protocol

from apollo.adapters.nhl_stats import NHLSeasonStatLine
from apollo.db import Database


class NHLAdvancedSeasonStatsAdapter(Protocol):
    def fetch_skater_advanced_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]: ...


@dataclass(frozen=True, slots=True)
class NHLAdvancedStatsSyncResult:
    skaters: int
    matched: int
    unmatched: int
    stats_written: int


def sync_nhl_advanced_stats(
    database: Database,
    adapter: NHLAdvancedSeasonStatsAdapter,
    season: int,
    game_type: int = 2,
) -> NHLAdvancedStatsSyncResult:
    database.initialize()
    skaters = adapter.fetch_skater_advanced_stats(season, game_type)

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

        for line in skaters:
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

    return NHLAdvancedStatsSyncResult(
        skaters=len(skaters),
        matched=matched,
        unmatched=unmatched,
        stats_written=stats_written,
    )

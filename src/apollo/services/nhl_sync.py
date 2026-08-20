from dataclasses import dataclass
from typing import Protocol

from apollo.db import Database
from apollo.models import NHLPlayerData


class NHLPlayerAdapter(Protocol):
    def find_player(
        self,
        first_name: str,
        last_name: str,
        team_abbrev: str | None = None,
    ) -> NHLPlayerData | None: ...

    def fetch_player(self, nhl_player_id: int) -> NHLPlayerData: ...


@dataclass(frozen=True, slots=True)
class NHLSyncResult:
    players: int
    matched: int
    unmatched: int
    stats_written: int


def sync_nhl_players(database: Database, adapter: NHLPlayerAdapter) -> NHLSyncResult:
    database.initialize()
    players = database.get_players_for_nhl_sync()
    with database.connect() as connection:
        rostered_player_ids = {
            int(row["player_id"])
            for row in connection.execute("SELECT DISTINCT player_id FROM roster").fetchall()
        }
    players = [player for player in players if int(player["id"]) in rostered_player_ids]

    matched = 0
    unmatched = 0
    stats_written = 0

    for player in players:
        nhl_external_id = player["nhl_external_id"]
        if nhl_external_id:
            profile = adapter.fetch_player(int(nhl_external_id))
        else:
            profile = adapter.find_player(
                str(player["first_name"]),
                str(player["last_name"]),
                str(player["nhl_team"]) if player["nhl_team"] else None,
            )

        if profile is None:
            unmatched += 1
            continue

        stats_written += database.upsert_nhl_player(int(player["id"]), profile)
        matched += 1

    return NHLSyncResult(
        players=len(players),
        matched=matched,
        unmatched=unmatched,
        stats_written=stats_written,
    )

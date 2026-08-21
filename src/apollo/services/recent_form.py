from dataclasses import dataclass
from typing import Protocol

from apollo.db import Database
from apollo.models import NHLGameLogEntry, NHLPlayerGameData


class NHLRecentFormAdapter(Protocol):
    def fetch_skater_game_stats(
        self,
        season: int,
        game_type: int = 2,
        page_size: int = 100,
    ) -> tuple[NHLPlayerGameData, ...]: ...

    def fetch_goalie_game_stats(
        self,
        season: int,
        game_type: int = 2,
        page_size: int = 100,
    ) -> tuple[NHLPlayerGameData, ...]: ...


@dataclass(frozen=True, slots=True)
class NHLRecentFormSyncResult:
    skater_rows: int
    goalie_rows: int
    matched_rows: int
    unmatched_rows: int
    players: int
    games: int
    stats_written: int


def _to_log_entry(row: NHLPlayerGameData) -> NHLGameLogEntry:
    return NHLGameLogEntry(
        game=row.game,
        team_abbrev=row.team_abbrev,
        opponent_abbrev=row.opponent_abbrev,
        home_road=row.home_road,
        stats=row.stats,
    )


def sync_nhl_recent_form(
    database: Database,
    adapter: NHLRecentFormAdapter,
    season: int,
    game_type: int = 2,
    page_size: int = 100,
) -> NHLRecentFormSyncResult:
    database.initialize()
    skaters = adapter.fetch_skater_game_stats(season, game_type, page_size)
    goalies = adapter.fetch_goalie_game_stats(season, game_type, page_size)
    rows = skaters + goalies

    with database.connect() as connection:
        identities = connection.execute(
            """
            SELECT player_id, external_id
            FROM player_external_id
            WHERE provider = 'nhl'
            """
        ).fetchall()
    player_ids = {int(row["external_id"]): int(row["player_id"]) for row in identities}

    grouped: dict[int, list[NHLGameLogEntry]] = {}
    unmatched_rows = 0
    matched_game_ids: set[int] = set()
    for row in rows:
        player_id = player_ids.get(row.nhl_player_id)
        if player_id is None:
            unmatched_rows += 1
            continue
        grouped.setdefault(player_id, []).append(_to_log_entry(row))
        matched_game_ids.add(row.game.game_id)

    stats_written = 0
    matched_rows = 0
    for player_id, entries in grouped.items():
        ordered = tuple(
            sorted(
                entries,
                key=lambda entry: (entry.game.game_date, entry.game.game_id),
            )
        )
        matched_rows += len(ordered)
        stats_written += database.replace_nhl_player_game_log(
            player_id,
            season,
            game_type,
            ordered,
        )

    return NHLRecentFormSyncResult(
        skater_rows=len(skaters),
        goalie_rows=len(goalies),
        matched_rows=matched_rows,
        unmatched_rows=unmatched_rows,
        players=len(grouped),
        games=len(matched_game_ids),
        stats_written=stats_written,
    )

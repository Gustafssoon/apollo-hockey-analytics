from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from apollo.db import Database
from apollo.models import NHLGame, NHLGameLogEntry, NHLPlayerData


class NHLCatalogAdapter(Protocol):
    def fetch_team_abbrevs(self) -> tuple[str, ...]: ...

    def fetch_roster(self, team_abbrev: str, season: int) -> tuple[NHLPlayerData, ...]: ...

    def fetch_schedule(self, team_abbrev: str, season: int) -> tuple[NHLGame, ...]: ...

    def fetch_game_log(
        self,
        nhl_player_id: int,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLGameLogEntry, ...]: ...


@dataclass(frozen=True, slots=True)
class NHLPlayerPoolSyncResult:
    teams: int
    players: int


@dataclass(frozen=True, slots=True)
class NHLScheduleSyncResult:
    team_abbrev: str
    games: int


@dataclass(frozen=True, slots=True)
class NHLLeagueScheduleSyncResult:
    teams: int
    games: int


@dataclass(frozen=True, slots=True)
class NHLGameLogSyncResult:
    player_name: str
    games: int
    stats_written: int


def _record_schedule_syncs(
    database: Database,
    season: int,
    game_counts: dict[str, int],
) -> None:
    fetched_at = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_schedule_sync (season, team_abbrev, fetched_at, game_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(season, team_abbrev) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                game_count = excluded.game_count
            """,
            [
                (season, team.upper(), fetched_at, game_count)
                for team, game_count in sorted(game_counts.items())
            ],
        )


def sync_nhl_player_pool(
    database: Database,
    adapter: NHLCatalogAdapter,
    season: int,
) -> NHLPlayerPoolSyncResult:
    database.initialize()
    teams = adapter.fetch_team_abbrevs()
    seen_player_ids: set[int] = set()

    for team in teams:
        for player in adapter.fetch_roster(team, season):
            database.upsert_nhl_pool_player(player)
            seen_player_ids.add(player.nhl_player_id)

    return NHLPlayerPoolSyncResult(teams=len(teams), players=len(seen_player_ids))


def sync_nhl_schedule(
    database: Database,
    adapter: NHLCatalogAdapter,
    team_abbrev: str,
    season: int,
) -> NHLScheduleSyncResult:
    database.initialize()
    team = team_abbrev.upper()
    games = adapter.fetch_schedule(team, season)
    database.upsert_nhl_games(games)
    _record_schedule_syncs(database, season, {team: len(games)})
    return NHLScheduleSyncResult(team_abbrev=team, games=len(games))


def sync_nhl_schedules(
    database: Database,
    adapter: NHLCatalogAdapter,
    season: int,
) -> NHLLeagueScheduleSyncResult:
    database.initialize()
    teams = adapter.fetch_team_abbrevs()
    games_by_id: dict[int, NHLGame] = {}
    game_counts: dict[str, int] = {}
    for raw_team in teams:
        team = raw_team.upper()
        team_games = adapter.fetch_schedule(team, season)
        game_counts[team] = len(team_games)
        for game in team_games:
            games_by_id[game.game_id] = game

    games = tuple(games_by_id[game_id] for game_id in sorted(games_by_id))
    database.upsert_nhl_games(games)
    _record_schedule_syncs(database, season, game_counts)
    return NHLLeagueScheduleSyncResult(teams=len(teams), games=len(games))


def sync_nhl_game_log(
    database: Database,
    adapter: NHLCatalogAdapter,
    player_name: str,
    season: int,
    game_type: int = 2,
) -> NHLGameLogSyncResult:
    database.initialize()
    identity = database.get_nhl_identity_by_name(player_name)
    if identity is None:
        raise LookupError(
            f'NHL identity not found for "{player_name}". '
            f'Run "apollo nhl pool --season {season}" first.'
        )

    entries = adapter.fetch_game_log(int(identity["nhl_external_id"]), season, game_type)
    stats_written = database.replace_nhl_player_game_log(
        int(identity["id"]),
        season,
        game_type,
        entries,
    )
    full_name = f"{identity['first_name']} {identity['last_name']}"
    return NHLGameLogSyncResult(
        player_name=full_name,
        games=len(entries),
        stats_written=stats_written,
    )

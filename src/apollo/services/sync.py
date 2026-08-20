from dataclasses import dataclass
from datetime import datetime, timezone

from apollo.adapters.base import LeagueAdapter
from apollo.db import Database
from apollo.models import PlayerSnapshot


@dataclass(frozen=True, slots=True)
class SyncResult:
    teams: int
    players: int
    roster_entries: int
    snapshots: int


def _get_league_id(connection, source: str, external_id: str, name: str) -> int:
    connection.execute(
        """
        INSERT INTO league (source, external_id, name)
        VALUES (?, ?, ?)
        ON CONFLICT(source, external_id) DO UPDATE SET name = excluded.name
        """,
        (source, external_id, name),
    )
    row = connection.execute(
        "SELECT id FROM league WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchone()
    return int(row["id"])


def _get_team_id(connection, league_id: int, external_id: str, name: str, is_user_team: bool) -> int:
    connection.execute(
        """
        INSERT INTO fantasy_team (league_id, external_id, name, is_user_team)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(league_id, external_id) DO UPDATE SET
            name = excluded.name,
            is_user_team = excluded.is_user_team
        """,
        (league_id, external_id, name, int(is_user_team)),
    )
    row = connection.execute(
        "SELECT id FROM fantasy_team WHERE league_id = ? AND external_id = ?",
        (league_id, external_id),
    ).fetchone()
    return int(row["id"])


def _get_player_id(connection, provider: str, player: PlayerSnapshot) -> int:
    row = connection.execute(
        """
        SELECT player_id
        FROM player_external_id
        WHERE provider = ? AND external_id = ?
        """,
        (provider, player.external_id),
    ).fetchone()

    if row is None:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, ?, ?, ?)
            """,
            (player.first_name, player.last_name, player.primary_position, player.nhl_team),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, ?, ?)
            """,
            (player_id, provider, player.external_id),
        )
        return player_id

    player_id = int(row["player_id"])
    connection.execute(
        """
        UPDATE player
        SET first_name = ?, last_name = ?, primary_position = ?, nhl_team = ?
        WHERE id = ?
        """,
        (player.first_name, player.last_name, player.primary_position, player.nhl_team, player_id),
    )
    return player_id


def sync_league(database: Database, adapter: LeagueAdapter) -> SyncResult:
    database.initialize()
    snapshot = adapter.fetch_league()
    captured_at = datetime.now(timezone.utc).isoformat()

    roster_count = 0

    with database.connect() as connection:
        league_id = _get_league_id(
            connection,
            snapshot.source,
            snapshot.external_id,
            snapshot.name,
        )

        for category in snapshot.stat_categories:
            connection.execute(
                """
                INSERT INTO league_stat_category (league_id, abbr, display_name)
                VALUES (?, ?, ?)
                ON CONFLICT(league_id, abbr) DO UPDATE SET display_name = excluded.display_name
                """,
                (league_id, category.abbr, category.display_name),
            )

        existing_team_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM fantasy_team WHERE league_id = ?", (league_id,)
            ).fetchall()
        ]
        if existing_team_ids:
            placeholders = ",".join("?" for _ in existing_team_ids)
            connection.execute(
                f"DELETE FROM roster WHERE fantasy_team_id IN ({placeholders})",
                existing_team_ids,
            )

        seen_players: set[int] = set()
        for team in snapshot.teams:
            team_id = _get_team_id(
                connection,
                league_id,
                team.external_id,
                team.name,
                team.is_user_team,
            )

            for player in team.players:
                player_id = _get_player_id(connection, snapshot.source, player)
                seen_players.add(player_id)
                connection.execute(
                    "INSERT INTO roster (fantasy_team_id, player_id, status) VALUES (?, ?, 'active')",
                    (team_id, player_id),
                )
                connection.execute(
                    """
                    INSERT INTO roster_snapshot (captured_at, fantasy_team_id, player_id, status)
                    VALUES (?, ?, ?, 'active')
                    """,
                    (captured_at, team_id, player_id),
                )
                roster_count += 1

    return SyncResult(
        teams=len(snapshot.teams),
        players=len(seen_players),
        roster_entries=roster_count,
        snapshots=roster_count,
    )

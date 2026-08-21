from dataclasses import dataclass
from datetime import UTC, datetime

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


def _reconcilable_player_id(connection, provider: str, player: PlayerSnapshot) -> int | None:
    if provider != "yahoo":
        return None

    rows = connection.execute(
        """
        SELECT
            p.id,
            CASE WHEN nhl.player_id IS NULL THEN 0 ELSE 1 END AS has_nhl_identity
        FROM player p
        LEFT JOIN player_external_id nhl
            ON nhl.player_id = p.id AND nhl.provider = 'nhl'
        WHERE LOWER(p.first_name) = LOWER(?)
          AND LOWER(p.last_name) = LOWER(?)
          AND (
              ? IS NULL
              OR p.nhl_team = ?
              OR p.nhl_team IS NULL
          )
        ORDER BY has_nhl_identity DESC, p.id
        """,
        (
            player.first_name,
            player.last_name,
            player.nhl_team,
            player.nhl_team,
        ),
    ).fetchall()
    if not rows:
        return None

    nhl_matches = [row for row in rows if int(row["has_nhl_identity"]) == 1]
    if len(nhl_matches) == 1:
        return int(nhl_matches[0]["id"])
    if len(rows) == 1:
        return int(rows[0]["id"])
    return None


def _set_provider_identity(
    connection,
    player_id: int,
    provider: str,
    external_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO player_external_id (player_id, provider, external_id)
        VALUES (?, ?, ?)
        ON CONFLICT(player_id, provider) DO UPDATE SET external_id = excluded.external_id
        """,
        (player_id, provider, external_id),
    )


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
        player_id = _reconcilable_player_id(connection, provider, player)
        if player_id is None:
            cursor = connection.execute(
                """
                INSERT INTO player (first_name, last_name, primary_position, nhl_team)
                VALUES (?, ?, ?, ?)
                """,
                (player.first_name, player.last_name, player.primary_position, player.nhl_team),
            )
            player_id = int(cursor.lastrowid)
        _set_provider_identity(connection, player_id, provider, player.external_id)
    else:
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
    captured_at = datetime.now(UTC).isoformat()

    roster_count = 0

    with database.connect() as connection:
        league_id = _get_league_id(
            connection,
            snapshot.source,
            snapshot.external_id,
            snapshot.name,
        )

        # League categories are current configuration, not historical rows. Replace them
        # on every sync so removed Yahoo categories cannot keep affecting league scoring.
        connection.execute(
            "DELETE FROM league_stat_category WHERE league_id = ?",
            (league_id,),
        )
        for category in snapshot.stat_categories:
            connection.execute(
                """
                INSERT INTO league_stat_category (league_id, abbr, display_name)
                VALUES (?, ?, ?)
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

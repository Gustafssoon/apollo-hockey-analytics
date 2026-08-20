import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from apollo.models import NHLPlayerData


class Database:
    def __init__(self, path: str | Path = "apollo.db") -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        schema = files("apollo.db").joinpath("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)

    def get_user_roster(self) -> list[sqlite3.Row]:
        query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                ft.name AS fantasy_team
            FROM roster r
            JOIN fantasy_team ft ON ft.id = r.fantasy_team_id
            JOIN player p ON p.id = r.player_id
            WHERE ft.is_user_team = 1
            ORDER BY p.primary_position, p.last_name, p.first_name
        """
        with self.connect() as connection:
            return connection.execute(query).fetchall()

    def get_players_for_nhl_sync(self) -> list[sqlite3.Row]:
        query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                nhl.external_id AS nhl_external_id
            FROM player p
            LEFT JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            ORDER BY p.id
        """
        with self.connect() as connection:
            return connection.execute(query).fetchall()

    def upsert_nhl_player(self, player_id: int, profile: NHLPlayerData) -> int:
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO player_external_id (player_id, provider, external_id)
                VALUES (?, 'nhl', ?)
                ON CONFLICT(player_id, provider) DO UPDATE SET external_id = excluded.external_id
                """,
                (player_id, str(profile.nhl_player_id)),
            )
            connection.execute(
                """
                UPDATE player
                SET nhl_team = COALESCE(?, nhl_team)
                WHERE id = ?
                """,
                (profile.team_abbrev, player_id),
            )
            connection.execute(
                """
                INSERT INTO nhl_player_profile (
                    player_id,
                    is_active,
                    team_abbrev,
                    position,
                    sweater_number,
                    birth_date,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    is_active = excluded.is_active,
                    team_abbrev = excluded.team_abbrev,
                    position = excluded.position,
                    sweater_number = excluded.sweater_number,
                    birth_date = excluded.birth_date,
                    fetched_at = excluded.fetched_at
                """,
                (
                    player_id,
                    int(profile.is_active),
                    profile.team_abbrev,
                    profile.position,
                    profile.sweater_number,
                    profile.birth_date,
                    fetched_at,
                ),
            )

            if profile.season is not None:
                connection.execute(
                    """
                    DELETE FROM nhl_player_season_stat
                    WHERE player_id = ? AND season = ? AND game_type = 2
                    """,
                    (player_id, profile.season),
                )
                connection.executemany(
                    """
                    INSERT INTO nhl_player_season_stat (
                        player_id,
                        season,
                        game_type,
                        stat_name,
                        value
                    )
                    VALUES (?, ?, 2, ?, ?)
                    """,
                    [
                        (player_id, profile.season, stat.name, stat.value)
                        for stat in profile.stats
                    ],
                )

        return len(profile.stats)

    def get_player_card(
        self, name: str
    ) -> tuple[sqlite3.Row, list[sqlite3.Row]] | None:
        profile_query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                nhl.external_id AS nhl_external_id,
                npp.is_active,
                npp.sweater_number,
                npp.birth_date,
                MAX(ns.season) AS season
            FROM player p
            LEFT JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            LEFT JOIN nhl_player_profile npp ON npp.player_id = p.id
            LEFT JOIN nhl_player_season_stat ns ON ns.player_id = p.id
            WHERE LOWER(p.first_name || ' ' || p.last_name) = LOWER(?)
            GROUP BY p.id
        """
        with self.connect() as connection:
            profile = connection.execute(profile_query, (name.strip(),)).fetchone()
            if profile is None:
                return None

            stats: list[sqlite3.Row] = []
            if profile["season"] is not None:
                stats = connection.execute(
                    """
                    SELECT stat_name, value
                    FROM nhl_player_season_stat
                    WHERE player_id = ? AND season = ? AND game_type = 2
                    ORDER BY stat_name
                    """,
                    (profile["id"], profile["season"]),
                ).fetchall()
            return profile, stats

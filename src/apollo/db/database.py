import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from apollo.models import NHLGame, NHLGameLogEntry, NHLPlayerData


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
            SELECT DISTINCT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                nhl.external_id AS nhl_external_id
            FROM roster r
            JOIN player p ON p.id = r.player_id
            LEFT JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            ORDER BY p.id
        """
        with self.connect() as connection:
            return connection.execute(query).fetchall()

    @staticmethod
    def _upsert_nhl_profile(
        connection: sqlite3.Connection,
        player_id: int,
        profile: NHLPlayerData,
        fetched_at: str,
    ) -> None:
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

    def upsert_nhl_player(self, player_id: int, profile: NHLPlayerData) -> int:
        fetched_at = datetime.now(UTC).isoformat()
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
            self._upsert_nhl_profile(connection, player_id, profile, fetched_at)

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

    def upsert_nhl_pool_player(self, profile: NHLPlayerData) -> int:
        fetched_at = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            identity = connection.execute(
                """
                SELECT player_id
                FROM player_external_id
                WHERE provider = 'nhl' AND external_id = ?
                """,
                (str(profile.nhl_player_id),),
            ).fetchone()

            if identity is not None:
                player_id = int(identity["player_id"])
            else:
                candidates = connection.execute(
                    """
                    SELECT p.id
                    FROM player p
                    LEFT JOIN player_external_id existing_nhl
                        ON existing_nhl.player_id = p.id
                       AND existing_nhl.provider = 'nhl'
                    WHERE LOWER(p.first_name) = LOWER(?)
                      AND LOWER(p.last_name) = LOWER(?)
                      AND (p.nhl_team = ? OR p.nhl_team IS NULL)
                      AND existing_nhl.player_id IS NULL
                    """,
                    (profile.first_name, profile.last_name, profile.team_abbrev),
                ).fetchall()
                if len(candidates) != 1:
                    candidates = connection.execute(
                        """
                        SELECT p.id
                        FROM player p
                        LEFT JOIN player_external_id existing_nhl
                            ON existing_nhl.player_id = p.id
                           AND existing_nhl.provider = 'nhl'
                        WHERE LOWER(p.first_name) = LOWER(?)
                          AND LOWER(p.last_name) = LOWER(?)
                          AND existing_nhl.player_id IS NULL
                        """,
                        (profile.first_name, profile.last_name),
                    ).fetchall()

                if len(candidates) == 1:
                    player_id = int(candidates[0]["id"])
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO player (
                            first_name,
                            last_name,
                            primary_position,
                            nhl_team
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            profile.first_name,
                            profile.last_name,
                            profile.position or "N/A",
                            profile.team_abbrev,
                        ),
                    )
                    player_id = int(cursor.lastrowid)

                connection.execute(
                    """
                    INSERT INTO player_external_id (player_id, provider, external_id)
                    VALUES (?, 'nhl', ?)
                    """,
                    (player_id, str(profile.nhl_player_id)),
                )

            connection.execute(
                """
                UPDATE player
                SET first_name = ?,
                    last_name = ?,
                    primary_position = COALESCE(?, primary_position),
                    nhl_team = COALESCE(?, nhl_team)
                WHERE id = ?
                """,
                (
                    profile.first_name,
                    profile.last_name,
                    profile.position,
                    profile.team_abbrev,
                    player_id,
                ),
            )
            self._upsert_nhl_profile(connection, player_id, profile, fetched_at)
            return player_id

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

    def get_nhl_identity_by_name(self, name: str) -> sqlite3.Row | None:
        query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.nhl_team,
                nhl.external_id AS nhl_external_id
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            WHERE LOWER(p.first_name || ' ' || p.last_name) = LOWER(?)
        """
        with self.connect() as connection:
            rows = connection.execute(query, (name.strip(),)).fetchall()
        return rows[0] if len(rows) == 1 else None

    def get_nhl_players(
        self,
        team_abbrev: str | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                nhl.external_id AS nhl_external_id,
                npp.sweater_number,
                npp.is_active
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            LEFT JOIN nhl_player_profile npp ON npp.player_id = p.id
            WHERE (? IS NULL OR p.nhl_team = ?)
            ORDER BY p.nhl_team, p.last_name, p.first_name
            LIMIT ?
        """
        team = team_abbrev.upper() if team_abbrev else None
        with self.connect() as connection:
            return connection.execute(query, (team, team, max(1, limit))).fetchall()

    @staticmethod
    def _upsert_game(
        connection: sqlite3.Connection,
        game: NHLGame,
        fetched_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO nhl_game (
                game_id,
                season,
                game_type,
                game_date,
                start_time_utc,
                away_team,
                home_team,
                game_state,
                fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                season = excluded.season,
                game_type = excluded.game_type,
                game_date = excluded.game_date,
                start_time_utc = COALESCE(excluded.start_time_utc, nhl_game.start_time_utc),
                away_team = COALESCE(excluded.away_team, nhl_game.away_team),
                home_team = COALESCE(excluded.home_team, nhl_game.home_team),
                game_state = COALESCE(excluded.game_state, nhl_game.game_state),
                fetched_at = excluded.fetched_at
            """,
            (
                game.game_id,
                game.season,
                game.game_type,
                game.game_date,
                game.start_time_utc,
                game.away_team,
                game.home_team,
                game.game_state,
                fetched_at,
            ),
        )

    def upsert_nhl_games(self, games: tuple[NHLGame, ...]) -> int:
        fetched_at = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            for game in games:
                self._upsert_game(connection, game, fetched_at)
        return len(games)

    def replace_nhl_player_game_log(
        self,
        player_id: int,
        season: int,
        game_type: int,
        entries: tuple[NHLGameLogEntry, ...],
    ) -> int:
        fetched_at = datetime.now(UTC).isoformat()
        stats_written = 0
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM nhl_player_game
                WHERE player_id = ?
                  AND game_id IN (
                      SELECT game_id
                      FROM nhl_game
                      WHERE season = ? AND game_type = ?
                  )
                """,
                (player_id, season, game_type),
            )
            for entry in entries:
                self._upsert_game(connection, entry.game, fetched_at)
                connection.execute(
                    """
                    INSERT INTO nhl_player_game (
                        player_id,
                        game_id,
                        team_abbrev,
                        opponent_abbrev,
                        home_road
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(player_id, game_id) DO UPDATE SET
                        team_abbrev = excluded.team_abbrev,
                        opponent_abbrev = excluded.opponent_abbrev,
                        home_road = excluded.home_road
                    """,
                    (
                        player_id,
                        entry.game.game_id,
                        entry.team_abbrev,
                        entry.opponent_abbrev,
                        entry.home_road,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO nhl_player_game_stat (
                        player_id,
                        game_id,
                        stat_name,
                        value
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (player_id, entry.game.game_id, stat.name, stat.value)
                        for stat in entry.stats
                    ],
                )
                stats_written += len(entry.stats)
        return stats_written

    def get_team_schedule(
        self,
        team_abbrev: str,
        season: int,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        team = team_abbrev.upper()
        query = """
            SELECT *
            FROM nhl_game
            WHERE season = ? AND (away_team = ? OR home_team = ?)
            ORDER BY game_date, start_time_utc
            LIMIT ?
        """
        with self.connect() as connection:
            return connection.execute(query, (season, team, team, max(1, limit))).fetchall()

    def get_player_game_log(
        self,
        name: str,
        season: int,
        limit: int = 10,
    ) -> list[tuple[sqlite3.Row, dict[str, float]]]:
        identity = self.get_nhl_identity_by_name(name)
        if identity is None:
            return []

        query = """
            SELECT
                g.game_id,
                g.game_date,
                pg.team_abbrev,
                pg.opponent_abbrev,
                pg.home_road
            FROM nhl_player_game pg
            JOIN nhl_game g ON g.game_id = pg.game_id
            WHERE pg.player_id = ? AND g.season = ?
            ORDER BY g.game_date DESC, g.game_id DESC
            LIMIT ?
        """
        with self.connect() as connection:
            games = connection.execute(
                query,
                (identity["id"], season, max(1, limit)),
            ).fetchall()
            result: list[tuple[sqlite3.Row, dict[str, float]]] = []
            for game in games:
                stat_rows = connection.execute(
                    """
                    SELECT stat_name, value
                    FROM nhl_player_game_stat
                    WHERE player_id = ? AND game_id = ?
                    """,
                    (identity["id"], game["game_id"]),
                ).fetchall()
                result.append(
                    (game, {str(row["stat_name"]): float(row["value"]) for row in stat_rows})
                )
            return result

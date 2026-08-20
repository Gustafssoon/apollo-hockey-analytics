import sqlite3
from importlib.resources import files
from pathlib import Path


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

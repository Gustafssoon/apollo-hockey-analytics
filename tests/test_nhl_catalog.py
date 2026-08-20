from apollo.adapters import NHLAdapter
from apollo.db import Database
from apollo.models import NHLGame, NHLGameLogEntry, NHLPlayerData, NHLStat
from apollo.services import sync_nhl_game_log, sync_nhl_player_pool, sync_nhl_schedule


def test_adapter_parses_team_roster_schedule_and_game_log():
    def fake_fetch_json(url: str):
        if url.endswith("/standings/now"):
            return {
                "standings": [
                    {"teamAbbrev": {"default": "EDM"}},
                    {"teamAbbrev": {"default": "COL"}},
                ]
            }
        if "/roster/EDM/" in url:
            return {
                "forwards": [
                    {
                        "id": 8478402,
                        "firstName": {"default": "Connor"},
                        "lastName": {"default": "McDavid"},
                        "positionCode": "C",
                        "sweaterNumber": 97,
                        "birthDate": "1997-01-13",
                    }
                ],
                "defensemen": [],
                "goalies": [],
            }
        if "/club-schedule-season/EDM/" in url:
            return {
                "games": [
                    {
                        "id": 2025020001,
                        "season": 20252026,
                        "gameType": 2,
                        "gameDate": "2025-10-08",
                        "startTimeUTC": "2025-10-09T02:00:00Z",
                        "gameState": "FUT",
                        "awayTeam": {"abbrev": "EDM"},
                        "homeTeam": {"abbrev": "CGY"},
                    }
                ]
            }
        if "/game-log/20252026/2" in url:
            return {
                "gameLog": [
                    {
                        "gameId": 2025020001,
                        "gameDate": "2025-10-08",
                        "teamAbbrev": "EDM",
                        "opponentAbbrev": "CGY",
                        "homeRoadFlag": "R",
                        "goals": 1,
                        "assists": 2,
                        "points": 3,
                        "shots": 5,
                        "toi": "21:34",
                    }
                ]
            }
        raise AssertionError(url)

    adapter = NHLAdapter(fetch_json=fake_fetch_json)
    assert adapter.fetch_team_abbrevs() == ("COL", "EDM")

    roster = adapter.fetch_roster("EDM", 20252026)
    assert roster[0].nhl_player_id == 8478402
    assert roster[0].position == "C"

    schedule = adapter.fetch_schedule("EDM", 20252026)
    assert schedule[0].away_team == "EDM"
    assert schedule[0].home_team == "CGY"

    game_log = adapter.fetch_game_log(8478402, 20252026)
    stats = {stat.name: stat.value for stat in game_log[0].stats}
    assert stats["points"] == 3
    assert stats["toiSeconds"] == 1294


def test_player_pool_sync_reuses_existing_player(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Connor', 'McDavid', 'C', 'EDM')
            """
        )

    class StubAdapter:
        def fetch_team_abbrevs(self):
            return ("EDM",)

        def fetch_roster(self, team_abbrev, season):
            return (
                NHLPlayerData(
                    nhl_player_id=8478402,
                    first_name="Connor",
                    last_name="McDavid",
                    team_abbrev=team_abbrev,
                    position="C",
                    is_active=True,
                    sweater_number=97,
                    birth_date="1997-01-13",
                    season=None,
                    stats=(),
                ),
                NHLPlayerData(
                    nhl_player_id=8477934,
                    first_name="Leon",
                    last_name="Draisaitl",
                    team_abbrev=team_abbrev,
                    position="C",
                    is_active=True,
                    sweater_number=29,
                    birth_date="1995-10-27",
                    season=None,
                    stats=(),
                ),
            )

    result = sync_nhl_player_pool(database, StubAdapter(), 20252026)
    assert result.teams == 1
    assert result.players == 2

    players = database.get_nhl_players("EDM")
    assert {row["last_name"] for row in players} == {"McDavid", "Draisaitl"}
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM player WHERE last_name = 'McDavid'"
        ).fetchone()["count"]
    assert count == 1


def test_player_pool_sync_does_not_overwrite_conflicting_nhl_identity(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Alex', 'Example', 'C', 'EDM')
            """
        )
        existing_player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', '111')
            """,
            (existing_player_id,),
        )

    new_player_id = database.upsert_nhl_pool_player(
        NHLPlayerData(
            nhl_player_id=222,
            first_name="Alex",
            last_name="Example",
            team_abbrev="EDM",
            position="C",
            is_active=True,
            sweater_number=10,
            birth_date="2000-01-01",
            season=None,
            stats=(),
        )
    )

    assert new_player_id != existing_player_id
    with database.connect() as connection:
        identities = connection.execute(
            """
            SELECT player_id, external_id
            FROM player_external_id
            WHERE provider = 'nhl'
            ORDER BY external_id
            """
        ).fetchall()

    assert [(row["player_id"], row["external_id"]) for row in identities] == [
        (existing_player_id, "111"),
        (new_player_id, "222"),
    ]


def test_schedule_and_game_log_persist(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    database.upsert_nhl_pool_player(
        NHLPlayerData(
            nhl_player_id=8478402,
            first_name="Connor",
            last_name="McDavid",
            team_abbrev="EDM",
            position="C",
            is_active=True,
            sweater_number=97,
            birth_date="1997-01-13",
            season=None,
            stats=(),
        )
    )

    game = NHLGame(
        game_id=2025020001,
        season=20252026,
        game_type=2,
        game_date="2025-10-08",
        start_time_utc="2025-10-09T02:00:00Z",
        away_team="EDM",
        home_team="CGY",
        game_state="FINAL",
    )
    entry = NHLGameLogEntry(
        game=game,
        team_abbrev="EDM",
        opponent_abbrev="CGY",
        home_road="R",
        stats=(NHLStat("goals", 1.0), NHLStat("points", 3.0)),
    )

    class StubAdapter:
        def fetch_schedule(self, team_abbrev, season):
            return (game,)

        def fetch_game_log(self, nhl_player_id, season, game_type=2):
            return (entry,)

    schedule_result = sync_nhl_schedule(database, StubAdapter(), "EDM", 20252026)
    assert schedule_result.games == 1
    assert len(database.get_team_schedule("EDM", 20252026)) == 1

    log_result = sync_nhl_game_log(
        database,
        StubAdapter(),
        "Connor McDavid",
        20252026,
    )
    assert log_result.games == 1
    assert log_result.stats_written == 2

    rows = database.get_player_game_log("Connor McDavid", 20252026)
    assert len(rows) == 1
    assert rows[0][1]["points"] == 3.0

from datetime import date

from apollo.analytics import build_waiver_board
from apollo.db import Database
from apollo.models import NHLGame, NHLGameLogEntry, NHLPlayerData, NHLStat
from apollo.services import sync_nhl_schedules

SEASON = 20252026
SCHEDULE_SEASON = 20262027


def _player(
    nhl_player_id: int,
    first_name: str,
    last_name: str,
    team: str,
    position: str = "C",
) -> NHLPlayerData:
    return NHLPlayerData(
        nhl_player_id=nhl_player_id,
        first_name=first_name,
        last_name=last_name,
        team_abbrev=team,
        position=position,
        is_active=True,
        sweater_number=None,
        birth_date=None,
        season=None,
        stats=(),
    )


def _seed_stats(
    database: Database,
    profile: NHLPlayerData,
    *,
    goals: float,
    assists: float,
    shots: float,
    hits: float,
    blocks: float,
) -> int:
    player_id = database.upsert_nhl_pool_player(profile)
    points = goals + assists
    database.upsert_nhl_player(
        player_id,
        NHLPlayerData(
            nhl_player_id=profile.nhl_player_id,
            first_name=profile.first_name,
            last_name=profile.last_name,
            team_abbrev=profile.team_abbrev,
            position=profile.position,
            is_active=True,
            sweater_number=None,
            birth_date=None,
            season=SEASON,
            stats=(
                NHLStat("gamesPlayed", 40),
                NHLStat("goals", goals),
                NHLStat("assists", assists),
                NHLStat("points", points),
                NHLStat("powerPlayPoints", points * 0.25),
                NHLStat("shots", shots),
                NHLStat("hits", hits),
                NHLStat("blockedShots", blocks),
            ),
        ),
    )
    return player_id


def _roster_player(database: Database, player_id: int) -> None:
    with database.connect() as connection:
        league_id = connection.execute(
            "INSERT INTO league (source, external_id, name) VALUES ('mock', 'league', 'League')"
        ).lastrowid
        team_id = connection.execute(
            """
            INSERT INTO fantasy_team (league_id, external_id, name, is_user_team)
            VALUES (?, 'team', 'Rostered Team', 1)
            """,
            (league_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO roster (fantasy_team_id, player_id) VALUES (?, ?)",
            (team_id, player_id),
        )


def _game(
    game_id: int,
    game_date: str,
    away: str,
    home: str,
) -> NHLGame:
    return NHLGame(
        game_id=game_id,
        season=SCHEDULE_SEASON,
        game_type=2,
        game_date=game_date,
        start_time_utc=None,
        away_team=away,
        home_team=home,
        game_state="FUT",
    )


def test_league_schedule_sync_deduplicates_games(tmp_path):
    database = Database(tmp_path / "apollo.db")

    class StubAdapter:
        def fetch_team_abbrevs(self):
            return ("EDM", "COL")

        def fetch_schedule(self, team_abbrev, season):
            assert season == SCHEDULE_SEASON
            shared = _game(1, "2026-10-10", "EDM", "COL")
            if team_abbrev == "EDM":
                return (shared, _game(2, "2026-10-12", "EDM", "CGY"))
            return (shared,)

    result = sync_nhl_schedules(database, StubAdapter(), SCHEDULE_SEASON)
    assert result.teams == 2
    assert result.games == 2
    with database.connect() as connection:
        stored = connection.execute(
            "SELECT COUNT(*) AS count FROM nhl_game WHERE season = ?",
            (SCHEDULE_SEASON,),
        ).fetchone()["count"]
    assert stored == 2


def test_waiver_board_excludes_rostered_and_uses_schedule_opportunity(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    alpha_id = _seed_stats(
        database,
        _player(1, "Alpha", "Star", "EDM"),
        goals=25,
        assists=35,
        shots=150,
        hits=40,
        blocks=30,
    )
    _seed_stats(
        database,
        _player(2, "Bravo", "Streamer", "COL"),
        goals=23,
        assists=33,
        shots=145,
        hits=42,
        blocks=32,
    )
    _seed_stats(
        database,
        _player(3, "Charlie", "Depth", "BOS"),
        goals=15,
        assists=20,
        shots=100,
        hits=35,
        blocks=25,
    )
    _roster_player(database, alpha_id)

    database.upsert_nhl_games(
        (
            _game(10, "2026-10-07", "COL", "EDM"),
            _game(11, "2026-10-09", "COL", "BOS"),
            _game(12, "2026-10-11", "COL", "EDM"),
            _game(13, "2026-10-13", "COL", "BOS"),
        )
    )

    board = build_waiver_board(
        database,
        SEASON,
        schedule_season=SCHEDULE_SEASON,
        as_of=date(2026, 10, 7),
        days=7,
        categories="G,A,SOG,HIT,BLK",
        limit=10,
    )

    assert board.schedule_complete is True
    assert board.schedule_team_count == 3
    assert board.expected_team_count == 3
    assert "Alpha Star" not in {player.name for player in board.players}
    bravo = next(player for player in board.players if player.name == "Bravo Streamer")
    assert bravo.schedule_games == 4
    assert bravo.off_night_games == 4
    assert bravo.schedule_component > 0


def test_waiver_board_adds_recent_trend_signal_when_game_log_is_stored(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    player_id = _seed_stats(
        database,
        _player(20, "Recent", "Riser", "EDM"),
        goals=20,
        assists=20,
        shots=120,
        hits=40,
        blocks=20,
    )
    _seed_stats(
        database,
        _player(21, "Stable", "Peer", "COL"),
        goals=20,
        assists=20,
        shots=120,
        hits=40,
        blocks=20,
    )

    entries = tuple(
        NHLGameLogEntry(
            game=NHLGame(
                game_id=100 + index,
                season=SEASON,
                game_type=2,
                game_date=f"2026-03-{index + 1:02d}",
                start_time_utc=None,
                away_team="EDM",
                home_team="COL",
                game_state="FINAL",
            ),
            team_abbrev="EDM",
            opponent_abbrev="COL",
            home_road="R",
            stats=(NHLStat("points", 2.0),),
        )
        for index in range(7)
    )
    database.replace_nhl_player_game_log(player_id, SEASON, 2, entries)

    board = build_waiver_board(
        database,
        SEASON,
        as_of=date(2026, 8, 21),
        categories="G,A,SOG,HIT,BLK",
        schedule_weight=0,
        trend_weight=0.5,
        limit=10,
    )
    riser = next(player for player in board.players if player.name == "Recent Riser")
    assert riser.trend == "UP"
    assert riser.trend_percent is not None
    assert riser.trend_percent > 0
    assert riser.trend_component == 0.5


def test_incomplete_schedule_disables_schedule_component(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    _seed_stats(
        database,
        _player(30, "One", "Player", "EDM"),
        goals=20,
        assists=30,
        shots=140,
        hits=50,
        blocks=30,
    )
    _seed_stats(
        database,
        _player(31, "Two", "Player", "COL"),
        goals=18,
        assists=28,
        shots=130,
        hits=45,
        blocks=28,
    )
    _seed_stats(
        database,
        _player(32, "Three", "Player", "BOS"),
        goals=16,
        assists=26,
        shots=120,
        hits=40,
        blocks=26,
    )
    database.upsert_nhl_games((_game(200, "2026-10-07", "EDM", "COL"),))

    board = build_waiver_board(
        database,
        SEASON,
        schedule_season=SCHEDULE_SEASON,
        as_of=date(2026, 10, 7),
        categories="G,A,SOG,HIT,BLK",
        limit=10,
    )

    assert board.schedule_complete is False
    assert board.schedule_team_count == 2
    assert board.expected_team_count == 3
    assert all(player.schedule_games is None for player in board.players)
    assert all(player.schedule_component == 0 for player in board.players)

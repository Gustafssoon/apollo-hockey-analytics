from datetime import date, timedelta

import pytest

from apollo.analytics import analyze_player
from apollo.db import Database
from apollo.models import NHLGame, NHLGameLogEntry, NHLPlayerData, NHLStat


SEASON = 20252026
SCHEDULE_SEASON = 20262027


def _seed_player(database: Database) -> int:
    database.initialize()
    return database.upsert_nhl_pool_player(
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


def _seed_game_log(database: Database, player_id: int) -> None:
    start = date(2026, 1, 1)
    entries: list[NHLGameLogEntry] = []
    for index in range(40):
        game_date = start + timedelta(days=index)
        is_recent = index >= 33
        points = 3.0 if is_recent else 1.0
        goals = 1.0 if is_recent else 0.0
        assists = points - goals
        entries.append(
            NHLGameLogEntry(
                game=NHLGame(
                    game_id=2025020000 + index,
                    season=SEASON,
                    game_type=2,
                    game_date=game_date.isoformat(),
                    start_time_utc=None,
                    away_team="EDM",
                    home_team="CGY",
                    game_state="FINAL",
                ),
                team_abbrev="EDM",
                opponent_abbrev="CGY",
                home_road="R",
                stats=(
                    NHLStat("goals", goals),
                    NHLStat("assists", assists),
                    NHLStat("points", points),
                    NHLStat("shots", 4.0 if is_recent else 2.0),
                    NHLStat("hits", 1.0),
                    NHLStat("blockedShots", 0.5),
                ),
            )
        )
    database.replace_nhl_player_game_log(player_id, SEASON, 2, tuple(entries))


def _seed_schedule(database: Database) -> None:
    games = (
        NHLGame(2026020001, SCHEDULE_SEASON, 2, "2026-10-01", None, "EDM", "CGY", "FUT"),
        NHLGame(2026020002, SCHEDULE_SEASON, 1, "2026-10-02", None, "EDM", "VAN", "FUT"),
        NHLGame(2026020003, SCHEDULE_SEASON, 2, "2026-10-03", None, "VAN", "EDM", "FUT"),
        NHLGame(2026020004, SCHEDULE_SEASON, 2, "2026-10-07", None, "EDM", "SJS", "FUT"),
        NHLGame(2026020005, SCHEDULE_SEASON, 2, "2026-10-08", None, "EDM", "LAK", "FUT"),
    )
    database.upsert_nhl_games(games)


def test_player_analysis_builds_rolling_windows_and_trend(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _seed_player(database)
    _seed_game_log(database, player_id)

    analysis = analyze_player(database, "Connor McDavid", SEASON)
    windows = {window.label: window for window in analysis.windows}

    assert windows["Season"].games == 40
    assert windows["Last 30"].games == 30
    assert windows["Last 14"].games == 14
    assert windows["Last 7"].games == 7
    assert windows["Season"].per_game["points"] == pytest.approx(1.35)
    assert windows["Last 30"].per_game["points"] == pytest.approx(44 / 30)
    assert windows["Last 14"].per_game["points"] == pytest.approx(2.0)
    assert windows["Last 7"].per_game["points"] == pytest.approx(3.0)
    assert analysis.trend_metric == "points"
    assert analysis.trend == "UP"
    assert analysis.trend_percent == pytest.approx(122.2222, rel=1e-4)


def test_player_analysis_counts_only_regular_season_schedule_games(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _seed_player(database)
    _seed_game_log(database, player_id)
    _seed_schedule(database)

    analysis = analyze_player(
        database,
        "Connor McDavid",
        SEASON,
        as_of=date(2026, 10, 1),
        schedule_season=SCHEDULE_SEASON,
        schedule_days=7,
    )

    assert analysis.schedule_start == date(2026, 10, 1)
    assert analysis.schedule_end == date(2026, 10, 7)
    assert analysis.upcoming_games == 3


def test_player_analysis_distinguishes_unsynced_schedule(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _seed_player(database)
    _seed_game_log(database, player_id)

    analysis = analyze_player(
        database,
        "Connor McDavid",
        SEASON,
        as_of=date(2026, 10, 1),
        schedule_season=SCHEDULE_SEASON,
    )

    assert analysis.upcoming_games is None


def test_player_analysis_requires_game_log(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_player(database)

    with pytest.raises(LookupError, match="No stored game log"):
        analyze_player(database, "Connor McDavid", SEASON)

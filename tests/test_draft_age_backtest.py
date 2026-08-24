import pytest

from apollo import cli_v15
from apollo.db import Database
from apollo.draft.age_backtest import AgeBacktestPlayer, build_age_backtest_result
from apollo.services.age_backtest import run_age_baseline_backtest


def _insert_player(
    database: Database,
    first_name: str,
    last_name: str,
    *,
    birth_date: str | None,
    position: str = "C",
) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, ?, ?, 'EDM')
            """,
            (first_name, last_name, position),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', ?)
            """,
            (player_id, str(9200000 + player_id)),
        )
        if birth_date is not None:
            connection.execute(
                """
                INSERT INTO nhl_player_profile (
                    player_id, is_active, team_abbrev, position, sweater_number, birth_date, fetched_at
                )
                VALUES (?, 1, 'EDM', ?, 97, ?, '2026-08-24T00:00:00Z')
                """,
                (player_id, position, birth_date),
            )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    goals: float,
    assists: float,
) -> None:
    stats = {
        "gamesPlayed": games,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": goals / 2,
        "shots": goals * 6,
        "hits": games,
        "blockedShots": games / 2,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            )
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, stat_name, value) for stat_name, value in stats.items()],
        )


def _seed_age_backtest(database: Database) -> None:
    dated = _insert_player(database, "Dated", "Skater", birth_date="1990-01-01")
    missing = _insert_player(database, "Missing", "Birthday", birth_date=None)
    for player_id in (dated, missing):
        _insert_season(database, player_id, 20222023, games=80, goals=40, assists=40)
        _insert_season(database, player_id, 20232024, games=80, goals=36, assists=36)
        _insert_season(database, player_id, 20242025, games=80, goals=32, assists=32)
        _insert_season(database, player_id, 20252026, games=80, goals=28, assists=28)


def test_neutral_age_strategy_reproduces_weighted_rate_projection():
    player = AgeBacktestPlayer(
        player_id=1,
        player_name="Neutral Player",
        position="C",
        projected_games=80.0,
        target_age=30.0,
        history=((29.0, 80.0, 80.0), (28.0, 80.0, 72.0), (27.0, 80.0, 64.0)),
        actual_points=72.0,
    )

    result = build_age_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
        season_weights=(0.6, 0.3, 0.1),
    )
    neutral = next(strategy for strategy in result.strategies if strategy.name == "neutral")

    assert neutral.points_mae == pytest.approx(abs(75.2 - 72.0))


def test_decline_strategy_can_improve_an_older_player_projection():
    player = AgeBacktestPlayer(
        player_id=1,
        player_name="Older Player",
        position="C",
        projected_games=80.0,
        target_age=35.0,
        history=((34.0, 80.0, 64.0), (33.0, 80.0, 72.0), (32.0, 80.0, 80.0)),
        actual_points=56.0,
    )

    result = build_age_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
        season_weights=(0.6, 0.3, 0.1),
    )
    metrics = {strategy.name: strategy for strategy in result.strategies}

    assert metrics["medium"].points_mae < metrics["neutral"].points_mae


def test_age_backtest_service_reports_birth_date_coverage(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_age_backtest(database)

    result = run_age_baseline_backtest(database, 20252026)

    assert result.base_eligible_players == 2
    assert result.evaluated_players == 1
    assert result.birth_date_coverage == pytest.approx(0.5)
    assert result.source_seasons == (20242025, 20232024, 20222023)


def test_age_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed_age_backtest(database)

    cli_v15.main(
        [
            "draft",
            "age-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )

    output = capsys.readouterr().out
    assert "APOLLO AGE BASELINE SHOOTOUT" in output
    assert "Target season: 2025-26" in output
    assert "Birth dates: 1/2 (50.0%)" in output
    assert "neutral" in output
    assert "late_peak" in output
    assert "no age strategy is promoted" in output

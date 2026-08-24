import pytest

from apollo import cli_v16
from apollo.db import Database
from apollo.draft.age_stat_backtest import (
    AGE_STAT_NAMES,
    AgeStatBacktestPlayer,
    AgeStatHistorySeason,
    build_age_stat_backtest_result,
)
from apollo.services.age_stat_backtest import run_age_stat_backtest


def _stats(*, goals: float, assists: float, games: float) -> dict[str, float]:
    return {
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": goals / 2,
        "shots": goals * 5,
        "hits": games,
        "blockedShots": games / 2,
    }


def _insert_player(
    database: Database,
    first_name: str,
    last_name: str,
    *,
    birth_date: str | None,
) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, ?, 'C', 'EDM')
            """,
            (first_name, last_name),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', ?)
            """,
            (player_id, str(9300000 + player_id)),
        )
        if birth_date is not None:
            connection.execute(
                """
                INSERT INTO nhl_player_profile (
                    player_id, is_active, team_abbrev, position, sweater_number, birth_date, fetched_at
                )
                VALUES (?, 1, 'EDM', 'C', 97, ?, '2026-08-24T00:00:00Z')
                """,
                (player_id, birth_date),
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
    values = {"gamesPlayed": games, **_stats(goals=goals, assists=assists, games=games)}
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            )
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, stat_name, value) for stat_name, value in values.items()],
        )


def _seed_service_data(database: Database) -> None:
    dated = _insert_player(database, "Dated", "Skater", birth_date="1992-01-01")
    missing = _insert_player(database, "Missing", "Birthday", birth_date=None)
    for player_id in (dated, missing):
        _insert_season(database, player_id, 20222023, games=80, goals=40, assists=40)
        _insert_season(database, player_id, 20232024, games=80, goals=36, assists=36)
        _insert_season(database, player_id, 20242025, games=80, goals=32, assists=32)
        _insert_season(database, player_id, 20252026, games=80, goals=28, assists=28)


def test_neutral_age_stat_strategy_reproduces_constant_rates():
    constant = _stats(goals=40.0, assists=40.0, games=80.0)
    player = AgeStatBacktestPlayer(
        player_id=1,
        player_name="Constant Rate",
        position="C",
        projected_games=80.0,
        target_age=30.0,
        history=(
            AgeStatHistorySeason(29.0, 80.0, constant),
            AgeStatHistorySeason(28.0, 80.0, constant),
            AgeStatHistorySeason(27.0, 80.0, constant),
        ),
        actual_stats=constant,
    )

    result = build_age_stat_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
        season_weights=(0.6, 0.3, 0.1),
    )
    neutral = [metric for metric in result.metrics if metric.strategy_name == "neutral"]

    assert len(neutral) == len(AGE_STAT_NAMES)
    assert all(metric.mae == pytest.approx(0.0) for metric in neutral)


def test_medium_age_strategy_can_reduce_goal_error_for_older_player():
    player = AgeStatBacktestPlayer(
        player_id=1,
        player_name="Older Scorer",
        position="C",
        projected_games=80.0,
        target_age=35.0,
        history=(
            AgeStatHistorySeason(34.0, 80.0, _stats(goals=32.0, assists=32.0, games=80.0)),
            AgeStatHistorySeason(33.0, 80.0, _stats(goals=36.0, assists=36.0, games=80.0)),
            AgeStatHistorySeason(32.0, 80.0, _stats(goals=40.0, assists=40.0, games=80.0)),
        ),
        actual_stats=_stats(goals=28.0, assists=28.0, games=80.0),
    )

    result = build_age_stat_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
        season_weights=(0.6, 0.3, 0.1),
    )
    goals = {
        metric.strategy_name: metric
        for metric in result.metrics
        if metric.stat_name == "goals"
    }

    assert goals["medium"].mae < goals["neutral"].mae


def test_age_stat_service_reports_birth_date_coverage(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_service_data(database)

    result = run_age_stat_backtest(database, 20252026)

    assert result.base_eligible_players == 2
    assert result.evaluated_players == 1
    assert result.birth_date_coverage == pytest.approx(0.5)
    assert len(result.metrics) == len(AGE_STAT_NAMES) * 6


def test_age_stat_cli_prints_best_strategy_by_stat(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed_service_data(database)

    cli_v16.main(
        [
            "draft",
            "age-stats-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )

    output = capsys.readouterr().out
    assert "APOLLO AGE STAT SHOOTOUT" in output
    assert "Birth dates: 1/2 (50.0%)" in output
    assert "PTS" in output
    assert "PPP" in output
    assert "BLK" in output
    assert "GAIN = neutral MAE - best MAE" in output

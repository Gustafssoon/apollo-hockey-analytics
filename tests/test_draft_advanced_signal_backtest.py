import pytest

from apollo import cli_v25
from apollo.db import Database
from apollo.draft.advanced_signal_backtest import (
    AdvancedSignalPlayer,
    build_advanced_signal_backtest_result,
    build_weighted_signals,
    signal_value,
)
from apollo.services.advanced_signal_backtest import run_advanced_signal_backtest


def test_cf60_uses_total_5v5_time_on_ice():
    value = signal_value(
        "cf60_5v5",
        games_played=80.0,
        stats={
            "shotAttemptsFor5v5": 1600.0,
            "timeOnIcePerGame5v5": 900.0,
        },
    )

    assert value == pytest.approx(80.0)


def test_weighted_signals_use_calendar_weights():
    history = (
        (20242025, 80.0, {"shotAttemptsPct5v5": 60.0}),
        (20232024, 80.0, {"shotAttemptsPct5v5": 50.0}),
        (20222023, 80.0, {"shotAttemptsPct5v5": 40.0}),
    )

    signals = build_weighted_signals(history)

    assert signals["sat_pct_5v5"] == pytest.approx(55.0)


def test_signal_screen_detects_positive_residual_relationship():
    players = tuple(
        AdvancedSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            baseline_goals=10.0,
            baseline_shots=100.0,
            actual_goals=10.0 + index,
            actual_shots=100.0 + index * 10.0,
            weighted_signals={"cf60_5v5": float(index)},
        )
        for index in range(1, 5)
    )

    result = build_advanced_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = next(metric for metric in result.metrics if metric.signal_name == "cf60_5v5")

    assert metric.evaluated_players == 4
    assert metric.goals_residual_rho == pytest.approx(1.0)
    assert metric.shots_residual_rho == pytest.approx(1.0)
    assert metric.goals_quartile_delta == pytest.approx(3.0)
    assert metric.shots_quartile_delta == pytest.approx(30.0)


def _insert_player(database: Database, index: int) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, 'Signal', 'C', 'EDM')
            """,
            (f"Player{index}",),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', ?)
            """,
            (player_id, str(9900000 + index)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    index: int,
    target: bool,
) -> None:
    games = 80.0
    stats = {
        "gamesPlayed": games,
        "goals": 20.0 + index + (2.0 if target else 0.0),
        "assists": 30.0 + index,
        "powerPlayPoints": 10.0 + index,
        "shots": 160.0 + index * 10.0 + (10.0 if target else 0.0),
        "hits": 50.0,
        "blockedShots": 40.0,
    }
    if not target:
        stats.update(
            {
                "shotAttemptsFor5v5": 1200.0 + index * 100.0,
                "unblockedShotAttemptsFor5v5": 900.0 + index * 80.0,
                "shotAttemptsPct5v5": 48.0 + index,
                "unblockedShotAttemptsPct5v5": 47.0 + index,
                "shotAttemptsRelative5v5": -2.0 + index,
                "unblockedShotAttemptsRelative5v5": -1.5 + index,
                "zoneStartPct5v5": 45.0 + index,
                "shootingPct5v5": 7.0 + index,
                "timeOnIcePerGame5v5": 900.0 + index * 20.0,
            }
        )
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_service_and_cli_screen_stored_advanced_history(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    for index in range(1, 5):
        player_id = _insert_player(database, index)
        for season in (20242025, 20232024, 20222023):
            _insert_season(database, player_id, season, index=index, target=False)
        _insert_season(database, player_id, 20252026, index=index, target=True)

    result = run_advanced_signal_backtest(database, 20252026)
    cf60 = next(metric for metric in result.metrics if metric.signal_name == "cf60_5v5")

    assert result.baseline_eligible_players == 4
    assert cf60.evaluated_players == 4

    cli_v25.main(
        [
            "draft",
            "advanced-signal-summary",
            "--season",
            "20252026",
            "--years",
            "1",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out
    assert "APOLLO ADVANCED SIGNAL SCREEN" in output
    assert "Baseline v0.4 player-seasons: 4" in output
    assert "CF60 5v5" in output
    assert "diagnostic screen only" in output

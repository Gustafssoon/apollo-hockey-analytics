import pytest

from apollo import cli_v45
from apollo.db import Database
from apollo.draft.goalie_workload_signal_backtest import (
    GoalieWorkloadSignalSeasonMetric,
    build_signal_aggregate,
    build_signal_metric,
)
from apollo.services.goalie_workload_signal_backtest import (
    _target_age,
    run_goalie_workload_signal_backtest,
)


def test_goalie_workload_signal_metric_detects_ordered_residual_signal():
    metric = build_signal_metric(
        "latest_start_share",
        20252026,
        ((0.20, -8.0), (0.30, -3.0), (0.50, 4.0), (0.70, 10.0)),
    )
    assert metric.player_seasons == 4
    assert metric.residual_rho == pytest.approx(1.0)
    assert metric.quartile_delta == pytest.approx(18.0)


def test_goalie_workload_signal_aggregate_preserves_year_signs_and_weighting():
    rows = (
        GoalieWorkloadSignalSeasonMetric("latest_start_share", 20252026, 4, 0.30, 6.0),
        GoalieWorkloadSignalSeasonMetric("latest_start_share", 20242025, 2, 0.10, 2.0),
        GoalieWorkloadSignalSeasonMetric("latest_start_share", 20232024, 2, -0.10, -2.0),
    )
    result = build_signal_aggregate((20252026, 20242025, 20232024), 8, rows)
    metric = next(item for item in result.metrics if item.signal_name == "latest_start_share")
    assert metric.player_seasons == 8
    assert metric.weighted_residual_rho == pytest.approx(0.15)
    assert metric.year_signs == "++-"
    assert metric.weighted_quartile_delta == pytest.approx(3.0)


def test_goalie_target_age_uses_target_season_start_reference():
    from datetime import date

    age = _target_age(date(2000, 10, 1), 20252026)
    assert age == pytest.approx(25.0, abs=0.01)


def _insert_goalie(database: Database, name: str) -> int:
    first, last = name.split(" ", 1)
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position, nhl_team) "
            "VALUES (?, ?, 'G', 'EDM')",
            (first, last),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) "
            "VALUES (?, 'nhl', ?)",
            (player_id, str(8800000 + player_id)),
        )
    return player_id


def _insert_season(database: Database, player_id: int, season: int, starts: float) -> None:
    stats = {
        "gamesStarted": starts,
        "wins": starts * 0.5,
        "saves": starts * 30.0,
        "goalsAgainst": starts * 2.5,
        "shutouts": starts * 0.08,
        "savePctg": 0.920,
        "goalsAgainstAvg": 2.50,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (player_id, season, game_type, stat_name, value)
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_goalie_workload_signal_service_keeps_strict_3of3_baseline(tmp_path):
    database = Database(tmp_path / "apollo.db")
    complete = _insert_goalie(database, "Complete Goalie")
    incomplete = _insert_goalie(database, "Incomplete Goalie")
    for player_id in (complete, incomplete):
        _insert_season(database, player_id, 20252026, 30.0)
        _insert_season(database, player_id, 20242025, 40.0)
        _insert_season(database, player_id, 20232024, 30.0)
    _insert_season(database, complete, 20222023, 20.0)

    baseline_n, metrics = run_goalie_workload_signal_backtest(
        database,
        20252026,
        min_actual_starts=20,
    )

    assert baseline_n == 1
    latest = next(item for item in metrics if item.signal_name == "latest_start_share")
    trend = next(item for item in metrics if item.signal_name == "start_share_trend")
    age = next(item for item in metrics if item.signal_name == "goalie_age")
    assert latest.player_seasons == 1
    assert trend.player_seasons == 1
    assert age.player_seasons == 0


def test_goalie_workload_signal_cli_contract():
    args = cli_v45.build_parser().parse_args(
        ["draft", "goalie-workload-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-workload-signal-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

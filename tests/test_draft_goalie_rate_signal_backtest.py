import pytest

from apollo import cli_v48
from apollo.db import Database
from apollo.draft.goalie_rate_signal_backtest import (
    GOALIE_RATE_SIGNALS,
    GoalieRateSignalSeasonMetric,
    build_signal_aggregate,
    build_signal_metric,
)
from apollo.services.goalie_rate_signal_backtest import run_goalie_rate_signal_backtest


def test_rate_signal_metric_tracks_residual_direction_and_quartiles():
    metric = build_signal_metric(
        "weighted_save_pct",
        20252026,
        ((0.900, 0.020), (0.910, 0.010), (0.920, -0.010), (0.930, -0.020)),
    )
    assert metric.player_seasons == 4
    assert metric.residual_rho == pytest.approx(-1.0)
    assert metric.quartile_delta == pytest.approx(-0.040)


def test_rate_signal_aggregate_locks_year_signs():
    season_metrics = tuple(
        GoalieRateSignalSeasonMetric(signal, season, 10, rho, delta)
        for signal in GOALIE_RATE_SIGNALS
        for season, rho, delta in (
            (20252026, -0.20, -0.01),
            (20242025, -0.10, -0.02),
            (20232024, 0.01, 0.00),
        )
    )
    result = build_signal_aggregate(
        (20252026, 20242025, 20232024),
        30,
        season_metrics,
    )
    assert all(metric.year_signs == "--0" for metric in result.metrics)
    assert all(metric.player_seasons == 30 for metric in result.metrics)


def _insert_goalie(database: Database, first: str, last: str) -> int:
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
            (player_id, str(9900000 + player_id)),
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
            INSERT INTO nhl_player_season_stat
                (player_id, season, game_type, stat_name, value)
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_rate_signal_service_preserves_strict_three_source_season_coverage(tmp_path):
    database = Database(tmp_path / "apollo.db")
    complete = _insert_goalie(database, "Complete", "Goalie")
    incomplete = _insert_goalie(database, "Incomplete", "Goalie")
    for season in (20252026, 20242025, 20232024, 20222023):
        _insert_season(database, complete, season, 30.0)
    for season in (20252026, 20242025, 20232024):
        _insert_season(database, incomplete, season, 30.0)

    player_seasons, metrics = run_goalie_rate_signal_backtest(database, 20252026)

    assert player_seasons == 1
    assert len(metrics) == 4
    assert all(metric.player_seasons == 1 for metric in metrics)


def test_rate_signal_service_uses_separate_sv_pct_and_gaa_residuals(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _insert_goalie(database, "Rate", "Goalie")
    for season in (20242025, 20232024, 20222023):
        _insert_season(database, player_id, season, 30.0)
    _insert_season(database, player_id, 20252026, 30.0)
    with database.connect() as connection:
        connection.execute(
            "UPDATE nhl_player_season_stat SET value = 0.900 "
            "WHERE player_id = ? AND season = 20252026 AND stat_name = 'savePctg'",
            (player_id,),
        )
        connection.execute(
            "UPDATE nhl_player_season_stat SET value = 3.50 "
            "WHERE player_id = ? AND season = 20252026 AND stat_name = 'goalsAgainstAvg'",
            (player_id,),
        )

    _, metrics = run_goalie_rate_signal_backtest(database, 20252026)

    assert {metric.signal_name for metric in metrics} == set(GOALIE_RATE_SIGNALS)


def test_rate_signal_variants_and_cli_contract():
    assert GOALIE_RATE_SIGNALS == (
        "weighted_save_pct",
        "latest_save_pct",
        "weighted_gaa",
        "latest_gaa",
    )
    args = cli_v48.build_parser().parse_args(
        ["draft", "goalie-rate-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-rate-signal-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

import pytest

from apollo import cli_v14
from apollo.db import Database
from apollo.draft.gp_backtest import (
    GPBacktestPlayer,
    build_gp_backtest_result,
    normalize_games_to_82,
    regular_season_game_limit,
)
from apollo.draft.projections import ProjectionError
from apollo.services.gp_backtest import run_gp_baseline_backtest


def _insert_player(database: Database, first_name: str, last_name: str) -> int:
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
            (player_id, str(9100000 + player_id)),
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


def _seed_gp_backtest(database: Database) -> None:
    alpha = _insert_player(database, "Alpha", "Skater")
    _insert_season(database, alpha, 20222023, games=40, goals=20, assists=20)
    _insert_season(database, alpha, 20232024, games=82, goals=41, assists=41)
    _insert_season(database, alpha, 20242025, games=82, goals=41, assists=41)
    _insert_season(database, alpha, 20252026, games=82, goals=42, assists=40)

    beta = _insert_player(database, "Beta", "Skater")
    _insert_season(database, beta, 20222023, games=70, goals=20, assists=30)
    _insert_season(database, beta, 20232024, games=65, goals=20, assists=30)
    _insert_season(database, beta, 20242025, games=60, goals=20, assists=30)
    _insert_season(database, beta, 20252026, games=62, goals=20, assists=30)

    incomplete = _insert_player(database, "Incomplete", "Skater")
    _insert_season(database, incomplete, 20232024, games=70, goals=20, assists=20)
    _insert_season(database, incomplete, 20242025, games=70, goals=20, assists=20)
    _insert_season(database, incomplete, 20252026, games=70, goals=20, assists=20)


def test_gp_shootout_can_identify_fixed_82_when_it_is_exact():
    players = (
        GPBacktestPlayer(1, "Alpha", 82.0, (82.0, 40.0, 82.0), 1.0, 82.0),
        GPBacktestPlayer(2, "Beta", 82.0, (40.0, 82.0, 82.0), 0.5, 41.0),
    )

    result = build_gp_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=players,
    )

    strategies = {strategy.name: strategy for strategy in result.strategies}
    assert strategies["fixed_82"].gp_mae == pytest.approx(0.0)
    assert strategies["fixed_82"].points_mae == pytest.approx(0.0)
    assert strategies["weighted_60_30_10"].gp_mae > 0


def test_games_normalization_uses_season_schedule_length():
    assert regular_season_game_limit(20202021) == 56
    assert regular_season_game_limit(20212022) == 82
    assert normalize_games_to_82(20202021, 56) == pytest.approx(82.0)
    assert normalize_games_to_82(20202021, 42) == pytest.approx(61.5)
    assert normalize_games_to_82(20222023, 41) == pytest.approx(41.0)


def test_gp_backtest_service_normalizes_shortened_history_season(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _insert_player(database, "Full", "Availability")
    _insert_season(database, player_id, 20202021, games=56, goals=28, assists=28)
    _insert_season(database, player_id, 20212022, games=82, goals=41, assists=41)
    _insert_season(database, player_id, 20222023, games=82, goals=41, assists=41)
    _insert_season(database, player_id, 20232024, games=82, goals=41, assists=41)

    result = run_gp_baseline_backtest(database, 20232024)
    strategies = {strategy.name: strategy for strategy in result.strategies}

    assert result.evaluated_players == 1
    assert result.source_seasons == (20222023, 20212022, 20202021)
    assert strategies["weighted_60_30_10"].gp_mae == pytest.approx(0.0)


def test_gp_backtest_service_requires_complete_three_season_history(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_gp_backtest(database)

    result = run_gp_baseline_backtest(database, 20252026)

    assert result.evaluated_players == 2
    assert result.source_seasons == (20242025, 20232024, 20222023)
    assert len(result.strategies) == 9


def test_gp_backtest_rejects_invalid_minimum_games(tmp_path):
    database = Database(tmp_path / "apollo.db")

    with pytest.raises(ProjectionError, match="min_actual_games must be >= 1"):
        run_gp_baseline_backtest(database, 20252026, min_actual_games=0)


def test_gp_backtest_rejects_shortened_target_season(tmp_path):
    database = Database(tmp_path / "apollo.db")

    with pytest.raises(ProjectionError, match="requires an 82-game target season"):
        run_gp_baseline_backtest(database, 20202021)


def test_gp_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed_gp_backtest(database)

    cli_v14.main(
        [
            "draft",
            "gp-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )

    output = capsys.readouterr().out
    assert "APOLLO GP BASELINE SHOOTOUT" in output
    assert "Target season: 2025-26" in output
    assert "Evaluated skaters: 2" in output
    assert "weighted_60_30_10" in output
    assert "fixed_82" in output
    assert "Ranked by PTS MAE" in output

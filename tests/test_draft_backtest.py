import pytest

from apollo import cli_v12
from apollo.db import Database
from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import ProjectionError
from apollo.services.draft_backtest import run_skater_backtest


def _insert_player(database: Database, first_name: str, last_name: str, position: str) -> int:
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
            (player_id, str(9000000 + player_id)),
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


def _seed_backtest(database: Database) -> None:
    alpha = _insert_player(database, "Alpha", "Skater", "C")
    for season in (20242025, 20232024, 20222023):
        _insert_season(database, alpha, season, games=80, goals=40, assists=60)
    _insert_season(database, alpha, 20252026, games=80, goals=42, assists=58)

    beta = _insert_player(database, "Beta", "Skater", "D")
    for season in (20242025, 20232024, 20222023):
        _insert_season(database, beta, season, games=60, goals=30, assists=30)
    _insert_season(database, beta, 20252026, games=70, goals=25, assists=30)

    gamma = _insert_player(database, "Gamma", "Skater", "LW")
    for season in (20242025, 20232024):
        _insert_season(database, gamma, season, games=75, goals=25, assists=35)
    _insert_season(database, gamma, 20252026, games=75, goals=30, assists=40)

    short = _insert_player(database, "Short", "Season", "RW")
    for season in (20242025, 20232024, 20222023):
        _insert_season(database, short, season, games=80, goals=20, assists=20)
    _insert_season(database, short, 20252026, games=10, goals=5, assists=5)

    goalie = _insert_player(database, "Goalie", "Player", "G")
    for season in (20242025, 20232024, 20222023, 20252026):
        _insert_season(database, goalie, season, games=50, goals=0, assists=0)


def test_spearman_rank_correlation_handles_order_and_reverse_order():
    assert spearman_rank_correlation([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
    assert spearman_rank_correlation([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)


def test_backtest_uses_only_actual_eligible_skaters_with_required_history(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_backtest(database)

    result = run_skater_backtest(database, 20252026)

    assert result.source_seasons == (20242025, 20232024, 20222023)
    assert result.actual_eligible_players == 3
    assert result.evaluated_players == 2
    assert result.coverage == pytest.approx(2 / 3)
    assert dict(result.history_counts) == {0: 0, 1: 0, 2: 1, 3: 2}

    metrics = {metric.stat_name: metric for metric in result.metrics}
    assert metrics["gamesPlayed"].mae == pytest.approx(5.0)
    assert metrics["goals"].mae == pytest.approx(3.5)
    assert metrics["points"].spearman_rho == pytest.approx(1.0)

    for overlap in result.top_k_points:
        assert overlap.compared_k == 2
        assert overlap.overlap == 2
        assert overlap.overlap_rate == pytest.approx(1.0)


def test_backtest_can_include_two_season_history(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_backtest(database)

    result = run_skater_backtest(database, 20252026, min_history_seasons=2)

    assert result.actual_eligible_players == 3
    assert result.evaluated_players == 3
    assert result.coverage == pytest.approx(1.0)


def test_backtest_rejects_invalid_filters(tmp_path):
    database = Database(tmp_path / "apollo.db")

    with pytest.raises(ProjectionError, match="min_actual_games must be >= 1"):
        run_skater_backtest(database, 20252026, min_actual_games=0)

    with pytest.raises(ProjectionError, match="min_history_seasons must be between 1 and 3"):
        run_skater_backtest(database, 20252026, min_history_seasons=4)


def test_draft_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed_backtest(database)

    cli_v12.main(
        [
            "draft",
            "backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )

    output = capsys.readouterr().out
    assert "APOLLO PROJECTION BACKTEST" in output
    assert "Target season: 2025-26" in output
    assert "Source seasons: 2024-25, 2023-24, 2022-23" in output
    assert "Evaluated: 2/3 (66.7%)" in output
    assert "GP        5.00" in output
    assert "G         3.50" in output
    assert "PTS      1.000" in output
    assert "Top 25  2/2 (100.0%)" in output

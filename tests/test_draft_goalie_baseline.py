import pytest

from apollo import cli_v43
from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BASELINE_VERSION,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_backtest


def _season_stats(starts: float, *, wins: float, saves: float, ga: float, so: float, svp: float, gaa: float):
    return {
        "gamesStarted": starts,
        "wins": wins,
        "saves": saves,
        "goalsAgainst": ga,
        "shutouts": so,
        "savePctg": svp,
        "goalsAgainstAvg": gaa,
    }


def test_goalie_projection_is_strict_603010_workload_and_rates():
    history = (
        (20252026, _season_stats(50.0, wins=30.0, saves=1500.0, ga=125.0, so=5.0, svp=0.923, gaa=2.5)),
        (20242025, _season_stats(40.0, wins=20.0, saves=1200.0, ga=120.0, so=4.0, svp=0.909, gaa=3.0)),
        (20232024, _season_stats(30.0, wins=15.0, saves=900.0, ga=90.0, so=3.0, svp=0.909, gaa=3.0)),
    )

    projection = build_goalie_projection(history)

    assert projection.model_version == GOALIE_BASELINE_VERSION
    assert projection.projected_starts == pytest.approx(45.0)
    assert projection.stats["wins"] == pytest.approx(25.2)
    assert projection.stats["saves"] == pytest.approx(1350.0)
    assert projection.stats["goalsAgainst"] == pytest.approx(121.5)
    assert projection.stats["shutouts"] == pytest.approx(4.5)
    assert projection.stats["savePctg"] == pytest.approx(0.9174)
    assert projection.stats["goalsAgainstAvg"] == pytest.approx(2.7)


def test_goalie_projection_requires_three_complete_positive_start_seasons():
    complete = _season_stats(40.0, wins=20.0, saves=1200.0, ga=100.0, so=4.0, svp=0.923, gaa=2.5)
    with pytest.raises(ProjectionError, match="exactly three source seasons"):
        build_goalie_projection(((20252026, complete), (20242025, complete)))

    no_starts = dict(complete)
    no_starts["gamesStarted"] = 0.0
    with pytest.raises(ProjectionError, match="positive starts"):
        build_goalie_projection(
            ((20252026, complete), (20242025, complete), (20232024, no_starts))
        )


def test_goalie_backtest_oracle_starts_isolates_workload_error():
    players = (
        GoalieBacktestPlayer(
            player_id=1,
            player_name="Goalie A",
            projected_starts=40.0,
            actual_starts=50.0,
            projected_stats={
                "wins": 20.0,
                "saves": 1200.0,
                "goalsAgainst": 100.0,
                "shutouts": 4.0,
                "savePctg": 0.920,
                "goalsAgainstAvg": 2.50,
            },
            actual_stats={
                "wins": 25.0,
                "saves": 1500.0,
                "goalsAgainst": 125.0,
                "shutouts": 5.0,
                "savePctg": 0.920,
                "goalsAgainstAvg": 2.50,
            },
        ),
        GoalieBacktestPlayer(
            player_id=2,
            player_name="Goalie B",
            projected_starts=20.0,
            actual_starts=30.0,
            projected_stats={
                "wins": 8.0,
                "saves": 600.0,
                "goalsAgainst": 60.0,
                "shutouts": 2.0,
                "savePctg": 0.900,
                "goalsAgainstAvg": 3.00,
            },
            actual_stats={
                "wins": 12.0,
                "saves": 900.0,
                "goalsAgainst": 90.0,
                "shutouts": 3.0,
                "savePctg": 0.900,
                "goalsAgainstAvg": 3.00,
            },
        ),
    )

    result = build_goalie_backtest_result(
        target_season=20252026,
        players=players,
        actual_eligible_goalies=2,
    )
    wins = next(metric for metric in result.metrics if metric.stat_name == "wins")
    saves = next(metric for metric in result.metrics if metric.stat_name == "saves")

    assert wins.oracle_starts_mae == pytest.approx(0.0)
    assert saves.oracle_starts_mae == pytest.approx(0.0)
    assert wins.mae > 0
    assert saves.mae > 0


def _insert_goalie(database: Database, name: str) -> int:
    first, last = name.split(" ", 1)
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position, nhl_team) VALUES (?, ?, 'G', 'EDM')",
            (first, last),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9900000 + player_id)),
        )
    return player_id


def _insert_goalie_season(database: Database, player_id: int, season: int, starts: float) -> None:
    stats = {
        "gamesPlayed": starts + 2.0,
        **_season_stats(
            starts,
            wins=starts * 0.5,
            saves=starts * 30.0,
            ga=starts * 2.5,
            so=starts * 0.08,
            svp=0.920,
            gaa=2.50,
        ),
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (player_id, season, game_type, stat_name, value)
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_goalie_backtest_coverage_keeps_incomplete_history_in_denominator(tmp_path):
    database = Database(tmp_path / "apollo.db")
    complete = _insert_goalie(database, "Complete Goalie")
    incomplete = _insert_goalie(database, "Incomplete Goalie")
    for player_id in (complete, incomplete):
        _insert_goalie_season(database, player_id, 20252026, 30.0)
        _insert_goalie_season(database, player_id, 20242025, 30.0)
        _insert_goalie_season(database, player_id, 20232024, 30.0)
    _insert_goalie_season(database, complete, 20222023, 30.0)

    result = run_goalie_baseline_backtest(database, 20252026, min_actual_starts=20)

    assert result.actual_eligible_goalies == 2
    assert result.evaluated_goalies == 1
    assert result.coverage == pytest.approx(0.5)


def test_goalie_baseline_cli_contract():
    args = cli_v43.build_parser().parse_args(
        ["draft", "goalie-baseline-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-baseline-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

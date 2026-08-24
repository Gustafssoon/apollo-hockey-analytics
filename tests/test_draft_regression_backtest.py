from apollo import cli_v21
from apollo.db import Database
from apollo.draft.regression_backtest import (
    REGRESSION_STRATEGIES,
    RegressionBacktestPlayer,
    RegressionHistorySeason,
    build_position_priors,
    build_regression_backtest_result,
)
from apollo.services.regression_backtest import run_regression_backtest


def _stats(scale: float = 1.0) -> dict[str, float]:
    return {
        "goals": 20.0 * scale,
        "assists": 30.0 * scale,
        "powerPlayPoints": 10.0 * scale,
        "shots": 160.0 * scale,
        "hits": 80.0 * scale,
        "blockedShots": 40.0 * scale,
    }


def test_position_priors_are_gp_weighted_and_split_forwards_defense():
    priors = build_position_priors(
        (
            (20242025, "C", 80.0, {"goals": 40.0}),
            (20242025, "L", 40.0, {"goals": 10.0}),
            (20242025, "D", 80.0, {"goals": 8.0}),
        )
    )

    assert priors[(20242025, "F", "goals")] == 50.0 / 120.0
    assert priors[(20242025, "D", "goals")] == 0.1


def test_more_pseudo_games_shrinks_extreme_rate_more():
    history = tuple(
        RegressionHistorySeason(
            season=season,
            games_played=80.0,
            stats=_stats(2.0),
        )
        for season in (20242025, 20232024, 20222023)
    )
    actual = _stats(1.0)
    actual["gamesPlayed"] = 80.0
    player = RegressionBacktestPlayer(
        player_id=1,
        player_name="Extreme Scorer",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_stats=_stats(2.0),
        history=history,
        actual_stats=actual,
    )
    priors = {
        (season, "F", stat): value / 80.0
        for season in (20242025, 20232024, 20222023)
        for stat, value in _stats(1.0).items()
    }

    result = build_regression_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        priors=priors,
    )
    strategies = {strategy.strategy_name: strategy for strategy in result.strategies}

    def goals(name: str) -> float:
        return next(
            metric.mae
            for metric in strategies[name].metrics
            if metric.stat_name == "goals"
        )

    assert set(strategies) == set(REGRESSION_STRATEGIES)
    assert goals("regress_pos_40") < goals("regress_pos_5") < goals("baseline_v03")


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
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9500000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    scale: float,
) -> None:
    stats = {"gamesPlayed": 80.0, **_stats(scale)}
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def _seed(database: Database) -> None:
    alpha = _insert_player(database, "Alpha", "Forward")
    beta = _insert_player(database, "Beta", "Forward")
    for season in (20222023, 20232024, 20242025):
        _insert_season(database, alpha, season, scale=1.0)
        _insert_season(database, beta, season, scale=2.0)
    _insert_season(database, alpha, 20252026, scale=20.0)
    _insert_season(database, beta, 20252026, scale=30.0)


def test_regression_service_priors_use_source_seasons_only(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_regression_backtest(database, 20252026)

    assert result.evaluated_players == 2
    assert result.source_seasons == (20242025, 20232024, 20222023)
    expected_goals_rate = (20.0 + 40.0) / (80.0 + 80.0)
    assert result.priors[(20242025, "F", "goals")] == expected_goals_rate


def test_regression_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    cli_v21.main(
        [
            "draft",
            "regression-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO REGRESSION-TO-MEAN SHOOTOUT" in output
    assert "source-season GP-weighted F/D rates only" in output
    assert "baseline_v03" in output
    assert "regress_pos_20" in output
    assert "No regression strategy is promoted automatically" in output

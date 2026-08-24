from apollo import cli_v19
from apollo.db import Database
from apollo.draft.deployment_backtest import (
    DEPLOYMENT_STRATEGIES,
    DeploymentBacktestPlayer,
    DeploymentHistorySeason,
    build_deployment_backtest_result,
)
from apollo.services.deployment_backtest import run_deployment_backtest


def _stats(scale: float = 1.0) -> dict[str, float]:
    return {
        "goals": 20.0 * scale,
        "assists": 30.0 * scale,
        "powerPlayPoints": 15.0 * scale,
        "shots": 160.0 * scale,
        "hits": 80.0 * scale,
        "blockedShots": 40.0 * scale,
    }


def test_actual_toi_oracle_can_capture_role_change():
    history = tuple(
        DeploymentHistorySeason(
            season=season,
            games_played=80.0,
            time_on_ice_per_game=10.0,
            stats=_stats(),
        )
        for season in (20242025, 20232024, 20222023)
    )
    actual = _stats(2.0)
    actual["gamesPlayed"] = 80.0
    actual["timeOnIcePerGame"] = 20.0
    player = DeploymentBacktestPlayer(
        player_id=1,
        player_name="Role Change",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_stats=_stats(),
        history=history,
        actual_time_on_ice_per_game=20.0,
        actual_stats=actual,
    )

    result = build_deployment_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
    )
    strategies = {strategy.strategy_name: strategy for strategy in result.strategies}
    baseline_pts = next(
        metric for metric in strategies["baseline_v03"].metrics if metric.stat_name == "points"
    )
    oracle_pts = next(
        metric
        for metric in strategies["actual_toi_oracle"].metrics
        if metric.stat_name == "points"
    )

    assert set(strategies) == set(DEPLOYMENT_STRATEGIES)
    assert oracle_pts.mae < baseline_pts.mae
    assert oracle_pts.mae == 0.0


def _insert_player(database: Database) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Test', 'Skater', 'C', 'EDM')
            """
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9300000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    toi: float,
    scale: float,
) -> None:
    stats = {
        "gamesPlayed": games,
        "timeOnIcePerGame": toi,
        **_stats(scale),
    }
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
    player_id = _insert_player(database)
    _insert_season(database, player_id, 20222023, games=80, toi=900, scale=0.90)
    _insert_season(database, player_id, 20232024, games=80, toi=960, scale=0.95)
    _insert_season(database, player_id, 20242025, games=80, toi=1020, scale=1.00)
    _insert_season(database, player_id, 20252026, games=80, toi=1080, scale=1.05)


def test_deployment_service_builds_v03_control_and_toi_strategies(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_deployment_backtest(database, 20252026)

    assert result.evaluated_players == 1
    assert result.base_eligible_players == 1
    assert result.toi_coverage == 1.0
    assert result.source_seasons == (20242025, 20232024, 20222023)
    assert {strategy.strategy_name for strategy in result.strategies} == set(DEPLOYMENT_STRATEGIES)


def test_deployment_weighted_toi_reports_toi_error(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_deployment_backtest(database, 20252026)
    weighted = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "toi_weighted"
    )
    oracle = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "actual_toi_oracle"
    )

    assert weighted.projected_toi_mae is not None
    assert weighted.projected_toi_mae > 0
    assert oracle.projected_toi_mae == 0.0


def test_deployment_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    cli_v19.main(
        [
            "draft",
            "deployment-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO DEPLOYMENT / TOI SHOOTOUT" in output
    assert "Control: apollo-skater-baseline-v0.3" in output
    assert "baseline_v03" in output
    assert "toi_weighted" in output
    assert "actual_toi_oracle" in output
    assert "Actual target TOI is diagnostic only" in output

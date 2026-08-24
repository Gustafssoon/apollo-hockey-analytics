from apollo import cli_v20
from apollo.db import Database
from apollo.draft.pp_usage_backtest import (
    PP_USAGE_STRATEGIES,
    PPUsageBacktestPlayer,
    PPUsageHistorySeason,
    build_pp_usage_backtest_result,
)
from apollo.services.pp_usage_backtest import run_pp_usage_backtest


def _history(pp_toi: float = 60.0, ppp: float = 8.0):
    return tuple(
        PPUsageHistorySeason(
            season=season,
            games_played=80.0,
            pp_time_on_ice_per_game=pp_toi,
            power_play_points=ppp,
        )
        for season in (20242025, 20232024, 20222023)
    )


def test_actual_pp_toi_oracle_can_capture_pp_role_change():
    player = PPUsageBacktestPlayer(
        player_id=1,
        player_name="PP Promotion",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_power_play_points=8.0,
        history=_history(),
        actual_pp_time_on_ice_per_game=120.0,
        actual_power_play_points=16.0,
    )

    result = build_pp_usage_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
    )
    strategies = {strategy.strategy_name: strategy for strategy in result.strategies}

    assert set(strategies) == set(PP_USAGE_STRATEGIES)
    assert strategies["actual_pp_toi_oracle"].ppp_mae == 0.0
    assert strategies["pp_toi_latest"].ppp_mae == 8.0
    assert strategies["baseline_v03"].ppp_mae == 8.0


def test_pp_toi_strategies_report_usage_error():
    player = PPUsageBacktestPlayer(
        player_id=1,
        player_name="PP Usage",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_power_play_points=10.0,
        history=(
            PPUsageHistorySeason(20242025, 80.0, 90.0, 12.0),
            PPUsageHistorySeason(20232024, 80.0, 60.0, 8.0),
            PPUsageHistorySeason(20222023, 80.0, 30.0, 4.0),
        ),
        actual_pp_time_on_ice_per_game=100.0,
        actual_power_play_points=13.0,
    )

    result = build_pp_usage_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
    )
    strategies = {strategy.strategy_name: strategy for strategy in result.strategies}

    assert strategies["pp_toi_latest"].pp_toi_mae == 10.0
    assert strategies["actual_pp_toi_oracle"].pp_toi_mae == 0.0
    assert strategies["pp_toi_weighted"].pp_toi_mae is not None


def _insert_player(database: Database) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Test', 'Powerplay', 'C', 'EDM')
            """
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9400000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    pp_toi: float,
    ppp: float,
) -> None:
    stats = {
        "gamesPlayed": 80.0,
        "goals": 20.0,
        "assists": 30.0,
        "powerPlayPoints": ppp,
        "shots": 160.0,
        "hits": 80.0,
        "blockedShots": 40.0,
        "powerPlayTimeOnIcePerGame": pp_toi,
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
    _insert_season(database, player_id, 20222023, pp_toi=60.0, ppp=8.0)
    _insert_season(database, player_id, 20232024, pp_toi=60.0, ppp=8.0)
    _insert_season(database, player_id, 20242025, pp_toi=60.0, ppp=8.0)
    _insert_season(database, player_id, 20252026, pp_toi=120.0, ppp=16.0)


def test_pp_usage_service_builds_same_sample_control(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_pp_usage_backtest(database, 20252026)

    assert result.base_eligible_players == 1
    assert result.evaluated_players == 1
    assert result.pp_history_coverage == 1.0
    assert result.source_seasons == (20242025, 20232024, 20222023)
    assert {strategy.strategy_name for strategy in result.strategies} == set(PP_USAGE_STRATEGIES)


def test_pp_usage_backtest_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    cli_v20.main(
        [
            "draft",
            "pp-usage-backtest",
            "--season",
            "20252026",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO PP USAGE SHOOTOUT" in output
    assert "PP history coverage: 1/1 (100.0%)" in output
    assert "baseline_v03" in output
    assert "pp_toi_latest" in output
    assert "actual_pp_toi_oracle" in output
    assert "Actual target PP TOI is diagnostic only" in output

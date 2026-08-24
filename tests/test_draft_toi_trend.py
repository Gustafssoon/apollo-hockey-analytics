from apollo.draft.deployment_backtest import (
    DeploymentBacktestPlayer,
    DeploymentHistorySeason,
    build_deployment_backtest_result,
)


def _stats() -> dict[str, float]:
    return {
        "goals": 20.0,
        "assists": 30.0,
        "powerPlayPoints": 15.0,
        "shots": 160.0,
        "hits": 80.0,
        "blockedShots": 40.0,
    }


def _player(*, tois: tuple[float, ...], actual_toi: float) -> DeploymentBacktestPlayer:
    seasons = (20242025, 20232024, 20222023)
    history = tuple(
        DeploymentHistorySeason(
            season=season,
            games_played=80.0,
            time_on_ice_per_game=toi,
            stats=_stats(),
        )
        for season, toi in zip(seasons, tois, strict=True)
    )
    actual = _stats()
    actual["gamesPlayed"] = 80.0
    actual["timeOnIcePerGame"] = actual_toi
    return DeploymentBacktestPlayer(
        player_id=1,
        player_name="Trend Player",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_stats=_stats(),
        history=history,
        actual_time_on_ice_per_game=actual_toi,
        actual_stats=actual,
    )


def _strategy(player: DeploymentBacktestPlayer, name: str):
    result = build_deployment_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
    )
    return next(strategy for strategy in result.strategies if strategy.strategy_name == name)


def test_recent_trend50_continues_half_of_latest_toi_change():
    strategy = _strategy(_player(tois=(1020.0, 960.0, 900.0), actual_toi=1080.0), "toi_recent_trend50")

    assert strategy.projected_toi_mae == 30.0


def test_recent_trend25_shrinks_latest_toi_change_more_aggressively():
    strategy = _strategy(_player(tois=(1020.0, 960.0, 900.0), actual_toi=1080.0), "toi_recent_trend25")

    assert strategy.projected_toi_mae == 45.0


def test_linear_trend_uses_multi_season_slope_instead_of_latest_jump():
    player = _player(tois=(1020.0, 900.0, 960.0), actual_toi=1030.0)
    recent = _strategy(player, "toi_recent_trend25")
    linear = _strategy(player, "toi_linear_trend25")

    assert linear.projected_toi_mae == 2.5
    assert recent.projected_toi_mae == 20.0


def test_toi_trend_projection_is_clamped_to_skater_maximum():
    strategy = _strategy(_player(tois=(1790.0, 1000.0, 900.0), actual_toi=1800.0), "toi_recent_trend50")

    assert strategy.projected_toi_mae == 0.0

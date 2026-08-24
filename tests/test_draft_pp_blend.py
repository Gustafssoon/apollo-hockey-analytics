from apollo.draft.pp_usage_backtest import (
    PP_BLEND_WEIGHTS,
    PPUsageBacktestPlayer,
    PPUsageHistorySeason,
    build_pp_usage_backtest_result,
)


def _player(*, baseline: float = 12.0, actual: float = 10.0) -> PPUsageBacktestPlayer:
    history = tuple(
        PPUsageHistorySeason(
            season=season,
            games_played=80.0,
            pp_time_on_ice_per_game=60.0,
            power_play_points=8.0,
        )
        for season in (20242025, 20232024, 20222023)
    )
    return PPUsageBacktestPlayer(
        player_id=1,
        player_name="Blend Test",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_power_play_points=baseline,
        history=history,
        actual_pp_time_on_ice_per_game=60.0,
        actual_power_play_points=actual,
    )


def _strategies(player: PPUsageBacktestPlayer):
    result = build_pp_usage_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
        base_eligible_players=1,
    )
    return {strategy.strategy_name: strategy for strategy in result.strategies}


def test_pp_blend_weights_are_locked():
    assert PP_BLEND_WEIGHTS == {
        "pp_blend_weighted25": 0.25,
        "pp_blend_weighted50": 0.50,
        "pp_blend_weighted75": 0.75,
    }


def test_pp_blends_interpolate_between_baseline_and_weighted_deployment():
    strategies = _strategies(_player())

    assert strategies["baseline_v03"].ppp_mae == 2.0
    assert strategies["pp_toi_weighted"].ppp_mae == 2.0
    assert strategies["pp_blend_weighted25"].ppp_mae == 1.0
    assert strategies["pp_blend_weighted50"].ppp_mae == 0.0
    assert strategies["pp_blend_weighted75"].ppp_mae == 1.0


def test_pp_blends_reuse_weighted_pp_toi_forecast():
    strategies = _strategies(_player())
    weighted = strategies["pp_toi_weighted"]

    for name in PP_BLEND_WEIGHTS:
        blend = strategies[name]
        assert blend.pp_toi_mae == weighted.pp_toi_mae
        assert blend.pp_toi_spearman_rho == weighted.pp_toi_spearman_rho

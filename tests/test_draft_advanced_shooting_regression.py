import pytest

from apollo.draft.advanced_shooting_regression import (
    ShootingRegressionPlayer,
    build_shooting_context_ratio,
    build_shooting_regression_aggregate_result,
    build_shooting_regression_backtest_result,
    correction_factor,
)


def _strategy(result, name: str):
    return next(strategy for strategy in result.strategies if strategy.strategy_name == name)


def _metric(strategy, stat_name: str):
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def test_correction_factor_reverts_extreme_shooting_context_toward_neutral():
    assert correction_factor(1.20, 0.25) == pytest.approx(0.95)
    assert correction_factor(0.80, 0.25) == pytest.approx(1.05)
    assert correction_factor(3.00, 0.50) == pytest.approx(0.80)


def test_shooting_context_ratio_uses_calendar_weights_and_requires_three_seasons():
    ratio = build_shooting_context_ratio(
        ((0.12, 0.10), (0.09, 0.10), (0.10, 0.10))
    )
    incomplete = build_shooting_context_ratio(
        ((0.12, 0.10), (0.09, 0.0), (0.10, 0.10))
    )

    assert ratio == pytest.approx(1.09)
    assert incomplete is None


def test_goals_25_correction_can_remove_known_shooting_context_error():
    players = (
        ShootingRegressionPlayer(
            player_id=1,
            player_name="Hot Context",
            baseline_goals=40.0,
            baseline_assists=40.0,
            actual_goals=38.0,
            actual_assists=40.0,
            shooting_context_ratio=1.20,
        ),
        ShootingRegressionPlayer(
            player_id=2,
            player_name="Cold Context",
            baseline_goals=40.0,
            baseline_assists=40.0,
            actual_goals=42.0,
            actual_assists=40.0,
            shooting_context_ratio=0.80,
        ),
    )
    result = build_shooting_regression_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=2,
        players=players,
    )

    baseline = _strategy(result, "baseline_v04")
    corrected = _strategy(result, "sh_goals_25")
    assert _metric(baseline, "points").mae == pytest.approx(2.0)
    assert _metric(corrected, "goals").mae == pytest.approx(0.0)
    assert _metric(corrected, "points").mae == pytest.approx(0.0)


def test_aggregate_tracks_improved_years_and_worst_year_gain():
    good_players = (
        ShootingRegressionPlayer(
            player_id=1,
            player_name="Hot",
            baseline_goals=40.0,
            baseline_assists=40.0,
            actual_goals=38.0,
            actual_assists=40.0,
            shooting_context_ratio=1.20,
        ),
        ShootingRegressionPlayer(
            player_id=2,
            player_name="Cold",
            baseline_goals=40.0,
            baseline_assists=40.0,
            actual_goals=42.0,
            actual_assists=40.0,
            shooting_context_ratio=0.80,
        ),
    )
    neutral_players = tuple(
        ShootingRegressionPlayer(
            player_id=player.player_id,
            player_name=player.player_name,
            baseline_goals=player.baseline_goals,
            baseline_assists=player.baseline_assists,
            actual_goals=player.baseline_goals,
            actual_assists=player.baseline_assists,
            shooting_context_ratio=player.shooting_context_ratio,
        )
        for player in good_players
    )
    results = (
        build_shooting_regression_backtest_result(
            target_season=20252026,
            source_seasons=(20242025, 20232024, 20222023),
            baseline_eligible_players=2,
            players=good_players,
        ),
        build_shooting_regression_backtest_result(
            target_season=20242025,
            source_seasons=(20232024, 20222023, 20212022),
            baseline_eligible_players=2,
            players=neutral_players,
        ),
    )
    aggregate = build_shooting_regression_aggregate_result(results)
    candidate = _strategy(aggregate, "sh_goals_25")

    assert candidate.points_improved_years == 1
    assert candidate.worst_points_mae_gain < 0

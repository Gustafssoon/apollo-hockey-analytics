import pytest

from apollo import cli_v30
from apollo.draft.scoring_rate_regression import (
    ScoringRateRegressionPlayer,
    build_rate_context_ratio,
    build_scoring_rate_regression_backtest_result,
    correction_factor,
)
from apollo.services.scoring_rate_regression import _build_rate_priors


def _metric(strategy, stat_name: str):
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def test_rate_correction_factor_uses_small_mean_reversion():
    assert correction_factor(1.20, 0.10) == pytest.approx(0.98)
    assert correction_factor(0.80, 0.10) == pytest.approx(1.02)


def test_rate_context_ratio_uses_calendar_weights_and_requires_three_seasons():
    history = ((1.2, 1.0), (0.9, 1.0), (1.0, 1.0))

    assert build_rate_context_ratio(history) == pytest.approx(1.09)
    assert build_rate_context_ratio(history[:2]) is None


def test_secondary_assist_signal_is_distinct_from_total_assist_signal():
    players = (
        ScoringRateRegressionPlayer(
            player_id=1,
            player_name="Hot Rates",
            baseline_goals=40.0,
            baseline_assists=60.0,
            actual_goals=38.0,
            actual_assists=57.0,
            g60_ratio=1.5,
            a60_ratio=1.0,
            secondary_a60_ratio=1.5,
        ),
        ScoringRateRegressionPlayer(
            player_id=2,
            player_name="Cold Rates",
            baseline_goals=20.0,
            baseline_assists=30.0,
            actual_goals=21.0,
            actual_assists=31.5,
            g60_ratio=0.5,
            a60_ratio=1.0,
            secondary_a60_ratio=0.5,
        ),
    )
    result = build_scoring_rate_regression_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=2,
        players=players,
    )
    baseline = next(strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v05")
    total_a = next(strategy for strategy in result.strategies if strategy.strategy_name == "a60_10")
    secondary_a = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "secondary_a60_10"
    )
    combined = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "g60_secondary_10"
    )

    assert _metric(total_a, "assists").mae == pytest.approx(_metric(baseline, "assists").mae)
    assert _metric(secondary_a, "assists").mae < _metric(baseline, "assists").mae
    assert _metric(combined, "points").mae == pytest.approx(0.0)


def test_rate_priors_are_weighted_by_5v5_toi_exposure_and_cli_contract():
    stats_by_player = {
        1: {
            20242025: {
                "gamesPlayed": 80.0,
                "timeOnIcePerGame5v5": 900.0,
                "goalsPer605v5": 1.2,
                "assistsPer605v5": 1.8,
                "secondaryAssistsPer605v5": 0.7,
            }
        },
        2: {
            20242025: {
                "gamesPlayed": 40.0,
                "timeOnIcePerGame5v5": 600.0,
                "goalsPer605v5": 0.6,
                "assistsPer605v5": 1.2,
                "secondaryAssistsPer605v5": 0.3,
            }
        },
    }
    priors = _build_rate_priors(stats_by_player, {1: "C", 2: "LW"}, (20242025,))
    exposure_1 = 80.0 * 900.0
    exposure_2 = 40.0 * 600.0

    assert priors[(20242025, "F", "goalsPer605v5")] == pytest.approx(
        (1.2 * exposure_1 + 0.6 * exposure_2) / (exposure_1 + exposure_2)
    )
    args = cli_v30.build_parser().parse_args(
        ["draft", "scoring-rate-regression-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "scoring-rate-regression-summary"
    assert args.season == 20252026

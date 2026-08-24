import pytest

from apollo import cli_v29
from apollo.draft.projections import ProjectionError
from apollo.draft.scoring_rate_signal_backtest import (
    ScoringRateSignalPlayer,
    build_scoring_rate_signal_aggregate_result,
    build_scoring_rate_signal_backtest_result,
    build_weighted_scoring_rate_signals,
    signal_value,
)


def _metric(result, signal_name: str):
    return next(metric for metric in result.metrics if metric.signal_name == signal_name)


def test_scoring_rate_signal_value_maps_official_stats_fields():
    stats = {
        "goalsPer605v5": 1.25,
        "primaryAssistsPer605v5": 0.75,
    }

    assert signal_value("g60_5v5", stats) == pytest.approx(1.25)
    assert signal_value("primary_a60_5v5", stats) == pytest.approx(0.75)
    assert signal_value("secondary_a60_5v5", stats) is None
    with pytest.raises(ProjectionError, match="Unknown scoring-rate signal"):
        signal_value("not_a_signal", stats)


def test_weighted_scoring_rates_use_calendar_weights_and_require_three_seasons():
    history = (
        {"goalsPer605v5": 1.0, "assistsPer605v5": 2.0},
        {"goalsPer605v5": 2.0, "assistsPer605v5": 3.0},
        {"goalsPer605v5": 3.0},
    )

    weighted = build_weighted_scoring_rate_signals(history)

    assert weighted["g60_5v5"] == pytest.approx(1.5)
    assert "a60_5v5" not in weighted


def test_scoring_rate_screen_detects_known_positive_goal_residual_signal():
    players = tuple(
        ScoringRateSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            baseline_goals=10.0,
            baseline_assists=20.0,
            actual_goals=10.0 + index,
            actual_assists=20.0,
            weighted_signals={"g60_5v5": float(index)},
        )
        for index in range(1, 5)
    )

    result = build_scoring_rate_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = _metric(result, "g60_5v5")

    assert metric.evaluated_players == 4
    assert metric.goals_residual_rho == pytest.approx(1.0)
    assert metric.goals_quartile_delta == pytest.approx(3.0)
    assert metric.points_residual_rho == pytest.approx(1.0)


def test_scoring_rate_aggregate_tracks_three_year_signs_and_cli_contract():
    def season_result(season: int, direction: float):
        players = tuple(
            ScoringRateSignalPlayer(
                player_id=index,
                player_name=f"Player {index}",
                baseline_goals=10.0,
                baseline_assists=20.0,
                actual_goals=10.0 + direction * index,
                actual_assists=20.0 + direction * index,
                weighted_signals={"pts60_5v5": float(index)},
            )
            for index in range(1, 5)
        )
        return build_scoring_rate_signal_backtest_result(
            target_season=season,
            source_seasons=(20242025, 20232024, 20222023),
            baseline_eligible_players=4,
            players=players,
        )

    aggregate = build_scoring_rate_signal_aggregate_result(
        (
            season_result(20252026, 1.0),
            season_result(20242025, 1.0),
            season_result(20232024, 1.0),
        )
    )
    metric = _metric(aggregate, "pts60_5v5")
    assert metric.points_year_signs == "+++"
    assert metric.weighted_points_residual_rho == pytest.approx(1.0)

    args = cli_v29.build_parser().parse_args(
        ["draft", "scoring-rate-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "scoring-rate-signal-summary"
    assert args.years == 3

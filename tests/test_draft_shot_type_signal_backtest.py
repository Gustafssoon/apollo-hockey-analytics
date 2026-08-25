import pytest

from apollo import cli_v32
from apollo.draft.projections import ProjectionError
from apollo.draft.shot_type_signal_backtest import (
    ShotTypeSignalPlayer,
    build_shot_type_signal_aggregate_result,
    build_shot_type_signal_backtest_result,
    build_weighted_shot_type_signals,
    signal_value,
)


def _metric(result, signal_name: str):
    return next(metric for metric in result.metrics if metric.signal_name == signal_name)


def test_shot_type_signal_values_cover_predefined_profile_candidates():
    stats = {
        "shotTypeShots": 200.0,
        "shotTypeGoals": 20.0,
        "shotTypeShootingPct": 0.10,
        "shotsOnNetTipIn": 20.0,
        "shotsOnNetDeflected": 10.0,
        "goalsTipIn": 4.0,
        "goalsDeflected": 2.0,
        "shotsOnNetWrist": 100.0,
        "goalsWrist": 8.0,
        "shootingPctWrist": 0.08,
        "shotsOnNetSnap": 50.0,
        "goalsSnap": 5.0,
        "shootingPctSnap": 0.10,
    }

    assert signal_value("tip_deflect_shot_share", stats) == pytest.approx(0.15)
    assert signal_value("wrist_shot_share", stats) == pytest.approx(0.50)
    assert signal_value("snap_shot_share", stats) == pytest.approx(0.25)
    assert signal_value("overall_shooting_pct", stats) == pytest.approx(0.10)
    assert signal_value("tip_deflect_shooting_pct", stats) == pytest.approx(0.20)
    assert signal_value("wrist_shooting_pct", stats) == pytest.approx(0.08)
    assert signal_value("snap_shooting_pct", stats) == pytest.approx(0.10)
    assert signal_value("tip_deflect_goal_share", stats) == pytest.approx(0.30)
    assert signal_value("wrist_goal_share", stats) == pytest.approx(0.40)
    assert signal_value("snap_goal_share", stats) == pytest.approx(0.25)

    with pytest.raises(ProjectionError, match="Unknown shot-type signal"):
        signal_value("not_a_signal", stats)


def test_weighted_shot_type_signals_use_source_weights_and_require_three_seasons():
    history = (
        {
            "shotTypeShots": 100.0,
            "shotsOnNetWrist": 50.0,
            "shootingPctWrist": 0.20,
        },
        {
            "shotTypeShots": 100.0,
            "shotsOnNetWrist": 40.0,
            "shootingPctWrist": 0.15,
        },
        {
            "shotTypeShots": 100.0,
            "shotsOnNetWrist": 30.0,
        },
    )

    weighted = build_weighted_shot_type_signals(history)

    assert weighted["wrist_shot_share"] == pytest.approx(0.45)
    assert "wrist_shooting_pct" not in weighted


def test_shot_type_screen_detects_known_positive_goal_residual_signal():
    players = tuple(
        ShotTypeSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            baseline_goals=10.0,
            baseline_assists=20.0,
            actual_goals=10.0 + index,
            actual_assists=20.0,
            weighted_signals={"wrist_shot_share": float(index)},
        )
        for index in range(1, 5)
    )

    result = build_shot_type_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = _metric(result, "wrist_shot_share")

    assert metric.evaluated_players == 4
    assert metric.goals_residual_rho == pytest.approx(1.0)
    assert metric.goals_quartile_delta == pytest.approx(3.0)
    assert metric.points_residual_rho == pytest.approx(1.0)


def test_missing_shot_type_context_does_not_reduce_baseline_coverage():
    players = tuple(
        ShotTypeSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            baseline_goals=10.0,
            baseline_assists=20.0,
            actual_goals=11.0,
            actual_assists=20.0,
            weighted_signals={"snap_shot_share": float(index)} if index <= 2 else {},
        )
        for index in range(1, 5)
    )

    result = build_shot_type_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )

    assert result.baseline_eligible_players == 4
    assert _metric(result, "snap_shot_share").evaluated_players == 2


def test_shot_type_aggregate_tracks_three_year_signs_and_cli_contract():
    def season_result(season: int, direction: float):
        players = tuple(
            ShotTypeSignalPlayer(
                player_id=index,
                player_name=f"Player {index}",
                baseline_goals=10.0,
                baseline_assists=20.0,
                actual_goals=10.0 + direction * index,
                actual_assists=20.0,
                weighted_signals={"tip_deflect_shot_share": float(index)},
            )
            for index in range(1, 5)
        )
        return build_shot_type_signal_backtest_result(
            target_season=season,
            source_seasons=(20242025, 20232024, 20222023),
            baseline_eligible_players=4,
            players=players,
        )

    aggregate = build_shot_type_signal_aggregate_result(
        (
            season_result(20252026, 1.0),
            season_result(20242025, 1.0),
            season_result(20232024, 1.0),
        )
    )
    metric = _metric(aggregate, "tip_deflect_shot_share")

    assert aggregate.baseline_player_seasons == 12
    assert metric.goals_year_signs == "+++"
    assert metric.weighted_goals_residual_rho == pytest.approx(1.0)

    args = cli_v32.build_parser().parse_args(
        ["draft", "shot-type-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "shot-type-signal-summary"
    assert args.years == 3
    assert args.min_history_seasons == 3

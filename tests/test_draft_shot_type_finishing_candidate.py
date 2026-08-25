import pytest

from apollo import cli_v33
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.projections import ProjectionError
from apollo.draft.shot_type_finishing_candidate import (
    SHOT_TYPE_FINISHING_SIGNALS,
    SHOT_TYPE_FINISHING_STRENGTHS,
    ShotTypeFinishingSeasonResult,
    ShotTypeFinishingVariantSeasonResult,
    build_shot_type_finishing_aggregate_result,
    candidate_model_version,
)
from apollo.services.shot_type_finishing_candidate import (
    _build_finishing_priors,
    _signal_exposure,
)


def _backtest(goals_offset: float):
    players = []
    for index in range(1, 5):
        projected_goals = 10.0 + goals_offset * index
        actual_goals = 10.0 + index
        projected_stats = {
            "goals": projected_goals,
            "assists": 20.0,
            "powerPlayPoints": 5.0,
            "shots": 100.0,
            "hits": 10.0,
            "blockedShots": 5.0,
        }
        actual_stats = {
            "goals": actual_goals,
            "assists": 20.0,
            "powerPlayPoints": 5.0,
            "shots": 100.0,
            "hits": 10.0,
            "blockedShots": 5.0,
        }
        players.append(
            BacktestPlayer(
                player_id=index,
                player_name=f"Player {index}",
                projected_games=82.0,
                actual_games=82.0,
                projected_stats=projected_stats,
                actual_stats=actual_stats,
            )
        )
    return build_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=tuple(players),
        actual_eligible_players=4,
        min_actual_games=20,
        min_history_seasons=3,
        history_counts=((3, 4),),
    )


def _metric(variant, stat_name: str):
    return next(metric for metric in variant.metrics if metric.stat_name == stat_name)


def test_candidate_model_versions_are_predefined():
    assert candidate_model_version("wrist_shooting_pct", 0.10).endswith(
        "wrist-shpct-shrink10"
    )
    with pytest.raises(ProjectionError, match="Unknown shot-type finishing signal"):
        candidate_model_version("not_a_signal", 0.10)
    with pytest.raises(ProjectionError, match="Unsupported shot-type finishing strength"):
        candidate_model_version("wrist_shooting_pct", 0.15)


def test_finishing_signal_exposure_uses_matching_shot_volume():
    stats = {
        "shots": 200.0,
        "shotsOnNetWrist": 100.0,
        "shotsOnNetSnap": 50.0,
        "shotsOnNetTipIn": 20.0,
        "shotsOnNetDeflected": 10.0,
    }

    assert _signal_exposure("overall_shooting_pct", stats) == pytest.approx(200.0)
    assert _signal_exposure("wrist_shooting_pct", stats) == pytest.approx(100.0)
    assert _signal_exposure("snap_shooting_pct", stats) == pytest.approx(50.0)
    assert _signal_exposure("tip_deflect_shooting_pct", stats) == pytest.approx(30.0)


def test_finishing_priors_are_source_only_and_exposure_weighted():
    stats_by_player = {
        1: {
            20242025: {"shootingPctWrist": 0.10, "shotsOnNetWrist": 100.0},
            20252026: {"shootingPctWrist": 0.99, "shotsOnNetWrist": 1000.0},
        },
        2: {
            20242025: {"shootingPctWrist": 0.20, "shotsOnNetWrist": 300.0},
            20252026: {"shootingPctWrist": 0.99, "shotsOnNetWrist": 1000.0},
        },
    }
    positions = {1: "C", 2: "LW"}

    priors = _build_finishing_priors(stats_by_player, positions, (20242025,))

    assert priors[(20242025, "F", "wrist_shooting_pct")] == pytest.approx(0.175)
    assert all(key[0] != 20252026 for key in priors)


def test_finishing_aggregate_tracks_goal_and_points_gains_without_touching_assists():
    baseline = _backtest(0.0)
    candidate = _backtest(1.0)

    def season_result(season: int):
        variants = tuple(
            ShotTypeFinishingVariantSeasonResult(
                signal_name=signal_name,
                strength=strength,
                model_version=candidate_model_version(signal_name, strength),
                result=candidate,
                applied=4,
            )
            for signal_name in SHOT_TYPE_FINISHING_SIGNALS
            for strength in SHOT_TYPE_FINISHING_STRENGTHS
        )
        return ShotTypeFinishingSeasonResult(
            target_season=season,
            baseline=baseline,
            variants=variants,
        )

    result = build_shot_type_finishing_aggregate_result(
        (
            season_result(20252026),
            season_result(20242025),
            season_result(20232024),
        )
    )
    variant = next(
        item
        for item in result.variants
        if item.signal_name == "wrist_shooting_pct" and item.strength == 0.10
    )

    assert variant.goals_improved_years == 3
    assert variant.points_improved_years == 3
    assert _metric(variant, "goals").mae_gain > 0
    assert _metric(variant, "points").mae_gain > 0
    assert _metric(variant, "assists").mae_gain == pytest.approx(0.0)


def test_shot_type_finishing_cli_contract():
    args = cli_v33.build_parser().parse_args(
        ["draft", "shot-type-finishing-candidate-summary", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "shot-type-finishing-candidate-summary"
    assert args.years == 3
    assert args.min_history_seasons == 3

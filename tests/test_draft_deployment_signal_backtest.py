import pytest

from apollo import cli_v36
from apollo.draft.deployment_signal_backtest import (
    DeploymentSignalPlayer,
    build_deployment_signal_aggregate_result,
    build_deployment_signal_backtest_result,
    build_weighted_deployment_signals,
    deployment_signal_value,
)
from apollo.draft.projections import ProjectionError
from apollo.services.deployment_signal_backtest import _build_deployment_priors


def _metric(result, signal_name: str, target_stat: str):
    return next(
        metric
        for metric in result.metrics
        if metric.signal_name == signal_name and metric.target_stat == target_stat
    )


def test_deployment_signal_values_map_usage_fields():
    stats = {
        "timeOnIcePerGame": 1200.0,
        "timeOnIcePerGame5v5": 900.0,
        "powerPlayTimeOnIcePerGame": 180.0,
    }

    assert deployment_signal_value("total_toi_ratio", stats) == pytest.approx(1200.0)
    assert deployment_signal_value("toi5v5_ratio", stats) == pytest.approx(900.0)
    assert deployment_signal_value("pp_toi_ratio", stats) == pytest.approx(180.0)
    assert deployment_signal_value("pp_toi_share_ratio", stats) == pytest.approx(0.15)
    with pytest.raises(ProjectionError, match="Unknown deployment signal"):
        deployment_signal_value("not_a_signal", stats)


def test_weighted_deployment_signals_use_source_priors_and_require_three_seasons():
    history = (
        {"timeOnIcePerGame": 1200.0, "powerPlayTimeOnIcePerGame": 180.0},
        {"timeOnIcePerGame": 1100.0, "powerPlayTimeOnIcePerGame": 160.0},
        {"timeOnIcePerGame": 1000.0},
    )
    priors = (
        {"total_toi_ratio": 1000.0, "pp_toi_ratio": 150.0},
        {"total_toi_ratio": 1000.0, "pp_toi_ratio": 150.0},
        {"total_toi_ratio": 1000.0, "pp_toi_ratio": 150.0},
    )

    weighted = build_weighted_deployment_signals(history, priors)

    assert weighted["total_toi_ratio"] == pytest.approx(1.15)
    assert "pp_toi_ratio" not in weighted


def test_deployment_priors_are_source_only_gp_weighted_and_split_by_position():
    stats_by_player = {
        1: {
            20242025: {"gamesPlayed": 80.0, "timeOnIcePerGame": 1200.0},
            20252026: {"gamesPlayed": 80.0, "timeOnIcePerGame": 9999.0},
        },
        2: {
            20242025: {"gamesPlayed": 40.0, "timeOnIcePerGame": 900.0},
            20252026: {"gamesPlayed": 80.0, "timeOnIcePerGame": 9999.0},
        },
        3: {
            20242025: {"gamesPlayed": 60.0, "timeOnIcePerGame": 1300.0},
        },
    }
    positions = {1: "C", 2: "LW", 3: "D"}

    priors = _build_deployment_priors(stats_by_player, positions, (20242025,))

    assert priors[(20242025, "F", "total_toi_ratio")] == pytest.approx(1100.0)
    assert priors[(20242025, "D", "total_toi_ratio")] == pytest.approx(1300.0)
    assert all(key[0] != 20252026 for key in priors)


def test_deployment_screen_detects_known_positive_ppp_residual_signal():
    players = tuple(
        DeploymentSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            projected_stats={
                "goals": 10.0,
                "assists": 20.0,
                "powerPlayPoints": 5.0,
                "shots": 100.0,
            },
            actual_stats={
                "goals": 10.0,
                "assists": 20.0,
                "powerPlayPoints": 5.0 + index,
                "shots": 100.0,
            },
            weighted_signals={"pp_toi_ratio": float(index)},
        )
        for index in range(1, 5)
    )

    result = build_deployment_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = _metric(result, "pp_toi_ratio", "powerPlayPoints")

    assert metric.evaluated_players == 4
    assert metric.residual_rho == pytest.approx(1.0)
    assert metric.quartile_delta == pytest.approx(3.0)


def test_deployment_aggregate_tracks_year_signs_and_cli_contract():
    def season_result(season: int, direction: float):
        players = tuple(
            DeploymentSignalPlayer(
                player_id=index,
                player_name=f"Player {index}",
                projected_stats={
                    "goals": 10.0,
                    "assists": 20.0,
                    "powerPlayPoints": 5.0,
                    "shots": 100.0,
                },
                actual_stats={
                    "goals": 10.0,
                    "assists": 20.0,
                    "powerPlayPoints": 5.0 + direction * index,
                    "shots": 100.0,
                },
                weighted_signals={"pp_toi_share_ratio": float(index)},
            )
            for index in range(1, 5)
        )
        return build_deployment_signal_backtest_result(
            target_season=season,
            source_seasons=(20242025, 20232024, 20222023),
            baseline_eligible_players=4,
            players=players,
        )

    result = build_deployment_signal_aggregate_result(
        (
            season_result(20252026, 1.0),
            season_result(20242025, 1.0),
            season_result(20232024, 1.0),
        )
    )
    metric = _metric(result, "pp_toi_share_ratio", "powerPlayPoints")

    assert result.baseline_player_seasons == 12
    assert metric.year_signs == "+++"
    assert metric.weighted_residual_rho == pytest.approx(1.0)

    args = cli_v36.build_parser().parse_args(
        ["draft", "deployment-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "deployment-signal-summary"
    assert args.years == 3
    assert args.min_history_seasons == 3

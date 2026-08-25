import pytest

from apollo import cli_v37
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.pp_deployment_candidate import (
    PP_DEPLOYMENT_SIGNALS,
    PP_DEPLOYMENT_STRENGTHS,
    PPDeploymentSeasonResult,
    PPDeploymentVariantSeasonResult,
    build_pp_deployment_aggregate_result,
    candidate_model_version,
)
from apollo.draft.projections import ProjectionError


def _backtest(*, candidate: bool):
    players = []
    for index in range(1, 5):
        projected_ppp = 5.0 + index if candidate else 5.0
        projected_stats = {
            "goals": 10.0,
            "assists": 20.0,
            "powerPlayPoints": projected_ppp,
            "shots": 100.0,
            "hits": 10.0,
            "blockedShots": 5.0,
        }
        actual_stats = {
            "goals": 10.0,
            "assists": 20.0,
            "powerPlayPoints": 5.0 + index,
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
    assert candidate_model_version("pp_toi_ratio", 0.05).endswith("pp-toi-shrink5")
    assert candidate_model_version("pp_toi_share_ratio", 0.20).endswith("pp-share-shrink20")
    with pytest.raises(ProjectionError, match="Unknown PP deployment signal"):
        candidate_model_version("total_toi_ratio", 0.05)
    with pytest.raises(ProjectionError, match="Unsupported PP deployment strength"):
        candidate_model_version("pp_toi_ratio", 0.15)


def test_candidate_grid_is_locked_to_two_pp_signals_and_three_strengths():
    assert PP_DEPLOYMENT_SIGNALS == ("pp_toi_ratio", "pp_toi_share_ratio")
    assert PP_DEPLOYMENT_STRENGTHS == (0.05, 0.10, 0.20)


def test_aggregate_tracks_ppp_gain_and_leaves_other_stats_exact():
    baseline = _backtest(candidate=False)
    candidate = _backtest(candidate=True)

    def season_result(season: int):
        variants = tuple(
            PPDeploymentVariantSeasonResult(
                signal_name=signal_name,
                strength=strength,
                model_version=candidate_model_version(signal_name, strength),
                result=candidate,
                applied=4,
            )
            for signal_name in PP_DEPLOYMENT_SIGNALS
            for strength in PP_DEPLOYMENT_STRENGTHS
        )
        return PPDeploymentSeasonResult(
            target_season=season,
            baseline=baseline,
            variants=variants,
        )

    result = build_pp_deployment_aggregate_result(
        (
            season_result(20252026),
            season_result(20242025),
            season_result(20232024),
        )
    )
    variant = next(
        item
        for item in result.variants
        if item.signal_name == "pp_toi_ratio" and item.strength == 0.05
    )

    assert result.baseline_player_seasons == 12
    assert variant.ppp_improved_years == 3
    assert variant.worst_ppp_mae_gain > 0
    assert _metric(variant, "powerPlayPoints").mae_gain > 0
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _metric(variant, stat_name)
        assert metric.mae_gain == pytest.approx(0.0)
        assert metric.baseline_rho == metric.candidate_rho


def test_aggregate_requires_season_results():
    with pytest.raises(ProjectionError, match="requires at least one season"):
        build_pp_deployment_aggregate_result(())


def test_pp_deployment_candidate_cli_contract():
    args = cli_v37.build_parser().parse_args(
        ["draft", "pp-deployment-ppp-candidate-summary", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "pp-deployment-ppp-candidate-summary"
    assert args.years == 3
    assert args.min_history_seasons == 3

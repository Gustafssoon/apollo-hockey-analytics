import pytest

from apollo import cli_v41
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.hit_regression_candidate import (
    HIT_REGRESSION_PSEUDO_GAMES,
    HitRegressionSeasonResult,
    HitRegressionVariantSeasonResult,
    build_hit_regression_aggregate_result,
    candidate_model_version,
)
from apollo.draft.projections import ProjectionError, ProjectionSeason
from apollo.services.hit_regression_candidate import _candidate_hits


def _backtest(*, candidate: bool):
    players = []
    for index in range(1, 5):
        projected_hits = 50.0 + index if candidate else 50.0
        projected_stats = {
            "goals": 10.0,
            "assists": 20.0,
            "powerPlayPoints": 5.0,
            "shots": 100.0,
            "hits": projected_hits,
            "blockedShots": 25.0,
        }
        actual_stats = {
            "goals": 10.0,
            "assists": 20.0,
            "powerPlayPoints": 5.0,
            "shots": 100.0,
            "hits": 50.0 + index,
            "blockedShots": 25.0,
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


def test_hit_candidate_grid_and_model_versions_are_predefined():
    assert HIT_REGRESSION_PSEUDO_GAMES == (5.0, 10.0, 20.0)
    assert candidate_model_version(5.0) == "apollo-regression-hits-pg5-candidate-v0.1"
    assert candidate_model_version(20.0) == "apollo-regression-hits-pg20-candidate-v0.1"
    with pytest.raises(ProjectionError, match="Unsupported HIT pseudo-games"):
        candidate_model_version(15.0)


def test_candidate_hits_changes_only_regression_strength():
    history = tuple(
        ProjectionSeason(
            season=season,
            games_played=80.0,
            stats={"hits": 160.0},
        )
        for season in (20252026, 20242025, 20232024)
    )
    priors = {
        (season, "F", "hits"): 1.0
        for season in (20252026, 20242025, 20232024)
    }

    hits, applied = _candidate_hits(
        history,
        birth_date=None,
        position="C",
        target_season=20262027,
        regression_priors=priors,
        projected_games=80.0,
        pseudo_games=10.0,
    )

    assert applied is True
    assert hits == pytest.approx((170.0 / 90.0) * 80.0)


def test_candidate_hits_without_prior_preserves_raw_rate():
    history = (
        ProjectionSeason(season=20252026, games_played=80.0, stats={"hits": 160.0}),
    )

    hits, applied = _candidate_hits(
        history,
        birth_date=None,
        position="C",
        target_season=20262027,
        regression_priors={},
        projected_games=82.0,
        pseudo_games=20.0,
    )

    assert applied is False
    assert hits == pytest.approx(2.0 * 82.0)


def test_aggregate_tracks_hit_gain_and_leaves_other_stats_exact():
    baseline = _backtest(candidate=False)
    candidate = _backtest(candidate=True)

    def season_result(season: int):
        variants = tuple(
            HitRegressionVariantSeasonResult(
                pseudo_games=pseudo_games,
                model_version=candidate_model_version(pseudo_games),
                result=candidate,
                applied=4,
            )
            for pseudo_games in HIT_REGRESSION_PSEUDO_GAMES
        )
        return HitRegressionSeasonResult(
            target_season=season,
            baseline=baseline,
            variants=variants,
        )

    result = build_hit_regression_aggregate_result(
        (
            season_result(20252026),
            season_result(20242025),
            season_result(20232024),
        )
    )
    variant = next(item for item in result.variants if item.pseudo_games == 5.0)

    assert result.baseline_player_seasons == 12
    assert variant.hit_improved_years == 3
    assert variant.worst_hit_mae_gain > 0
    assert _metric(variant, "hits").mae_gain > 0
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "powerPlayPoints",
        "shots",
        "blockedShots",
    ):
        metric = _metric(variant, stat_name)
        assert metric.mae_gain == pytest.approx(0.0)
        assert metric.baseline_rho == metric.candidate_rho


def test_hit_regression_candidate_cli_contract():
    args = cli_v41.build_parser().parse_args(
        ["draft", "hit-regression-candidate-summary", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "hit-regression-candidate-summary"
    assert args.years == 3
    assert args.min_actual_games == 20
    assert args.min_history_seasons == 3

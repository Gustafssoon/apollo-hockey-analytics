import pytest

from apollo import cli_v38
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.pp_deployment_candidate_gate import (
    PP_DEPLOYMENT_CANDIDATE_SIGNAL,
    PP_DEPLOYMENT_CANDIDATE_STRENGTH,
    PP_DEPLOYMENT_CANDIDATE_VERSION,
    PP_DEPLOYMENT_ROBUSTNESS_COHORTS,
    build_pp_deployment_gate_cohort,
)
from apollo.draft.projections import ProjectionError
from apollo.services.pp_deployment_candidate import run_pp_deployment_candidate_backtest


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


def _metric(cohort, stat_name: str):
    return next(metric for metric in cohort.metrics if metric.stat_name == stat_name)


def test_pp_deployment_gate_candidate_and_cohorts_are_locked():
    assert PP_DEPLOYMENT_CANDIDATE_SIGNAL == "pp_toi_ratio"
    assert PP_DEPLOYMENT_CANDIDATE_STRENGTH == 0.05
    assert PP_DEPLOYMENT_CANDIDATE_VERSION.endswith("pp-toi-shrink5")
    assert PP_DEPLOYMENT_ROBUSTNESS_COHORTS == (
        ("GP20 ALL", 20, None),
        ("GP30 ALL", 30, None),
        ("GP40 ALL", 40, None),
        ("GP20 F", 20, "F"),
        ("GP20 D", 20, "D"),
    )


def test_pp_deployment_gate_cohort_tracks_ppp_and_other_exact():
    baseline = _backtest(candidate=False)
    candidate = _backtest(candidate=True)
    cohort = build_pp_deployment_gate_cohort(
        label="GP20 ALL",
        min_actual_games=20,
        position_group=None,
        season_results=((baseline, candidate, 4),) * 3,
    )

    assert cohort.player_seasons == 12
    assert cohort.applied == 12
    assert cohort.ppp_improved_years == 3
    assert cohort.worst_ppp_gain > 0
    assert _metric(cohort, "powerPlayPoints").mae_gain > 0
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _metric(cohort, stat_name)
        assert metric.mae_gain == pytest.approx(0.0)
        assert metric.baseline_rho == metric.candidate_rho


def test_pp_deployment_backtest_rejects_invalid_position_filter(tmp_path):
    from apollo.db import Database

    with pytest.raises(ProjectionError, match="position_group_filter must be F, D, or None"):
        run_pp_deployment_candidate_backtest(
            Database(tmp_path / "apollo.db"),
            20252026,
            position_group_filter="G",
        )


def test_pp_deployment_candidate_gate_cli_contract():
    args = cli_v38.build_parser().parse_args(
        ["draft", "pp-deployment-candidate-gate", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "pp-deployment-candidate-gate"
    assert args.years == 3
    assert args.min_history_seasons == 3

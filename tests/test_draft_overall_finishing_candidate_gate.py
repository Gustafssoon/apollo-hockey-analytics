import pytest

from apollo import cli_v34
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.overall_finishing_candidate_gate import (
    OVERALL_SHOOTING_CANDIDATE_VERSION,
    OVERALL_SHOOTING_SIGNAL,
    OVERALL_SHOOTING_STRENGTH,
    ROBUSTNESS_COHORTS,
    build_overall_finishing_gate_cohort,
    build_overall_finishing_gate_result,
)


def _backtest(target_season: int, goal_scale: float, *, model_version: str | None = None):
    players = []
    for index in range(1, 9):
        actual_goals = 10.0 + index
        projected_goals = 10.0 + goal_scale * index
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
    kwargs = {}
    if model_version is not None:
        kwargs["model_version"] = model_version
    return build_backtest_result(
        target_season=target_season,
        source_seasons=(20242025, 20232024, 20222023),
        players=tuple(players),
        actual_eligible_players=len(players),
        min_actual_games=20,
        min_history_seasons=3,
        history_counts=((3, len(players)),),
        **kwargs,
    )


def _metric(cohort, stat_name: str):
    return next(metric for metric in cohort.metrics if metric.stat_name == stat_name)


def test_overall_sh5_gate_contract_is_locked_before_robustness_run():
    assert OVERALL_SHOOTING_SIGNAL == "overall_shooting_pct"
    assert OVERALL_SHOOTING_STRENGTH == pytest.approx(0.05)
    assert OVERALL_SHOOTING_CANDIDATE_VERSION.endswith("overall-shpct-shrink5")
    assert ROBUSTNESS_COHORTS == (
        ("GP20 ALL", 20, None),
        ("GP30 ALL", 30, None),
        ("GP40 ALL", 40, None),
        ("GP20 F", 20, "F"),
        ("GP20 D", 20, "D"),
    )


def test_gate_cohort_tracks_three_year_goal_and_points_robustness():
    seasons = (20252026, 20242025, 20232024)
    season_results = tuple(
        (
            _backtest(season, 0.0),
            _backtest(
                season,
                0.5,
                model_version=OVERALL_SHOOTING_CANDIDATE_VERSION,
            ),
            8,
        )
        for season in seasons
    )

    cohort = build_overall_finishing_gate_cohort(
        label="GP20 ALL",
        min_actual_games=20,
        position_group=None,
        season_results=season_results,
    )

    assert cohort.player_seasons == 24
    assert cohort.applied == 24
    assert cohort.goals_improved_years == 3
    assert cohort.points_improved_years == 3
    assert cohort.worst_goals_gain > 0
    assert cohort.worst_points_gain > 0
    assert _metric(cohort, "goals").mae_gain > 0
    assert _metric(cohort, "points").mae_gain > 0


def test_gate_candidate_leaves_non_goal_categories_exact():
    seasons = (20252026, 20242025, 20232024)
    cohort = build_overall_finishing_gate_cohort(
        label="GP20 D",
        min_actual_games=20,
        position_group="D",
        season_results=tuple(
            (
                _backtest(season, 0.0),
                _backtest(
                    season,
                    0.5,
                    model_version=OVERALL_SHOOTING_CANDIDATE_VERSION,
                ),
                8,
            )
            for season in seasons
        ),
    )

    for stat_name in (
        "gamesPlayed",
        "assists",
        "powerPlayPoints",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _metric(cohort, stat_name)
        assert metric.mae_gain == pytest.approx(0.0)
        assert metric.baseline_rho == metric.candidate_rho

    result = build_overall_finishing_gate_result(
        latest_target_season=20252026,
        target_seasons=seasons,
        cohorts=(cohort,),
    )
    assert result.target_seasons == seasons


def test_overall_finishing_candidate_gate_cli_contract():
    args = cli_v34.build_parser().parse_args(
        ["draft", "overall-finishing-candidate-gate", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "overall-finishing-candidate-gate"
    assert args.years == 3
    assert args.min_history_seasons == 3

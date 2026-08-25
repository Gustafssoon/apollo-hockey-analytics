import pytest

from apollo import cli_v44
from apollo.draft.goalie_baseline import GoalieBacktestPlayer
from apollo.draft.goalie_workload_candidate import (
    apply_workload_to_baseline,
    project_workload_starts,
    scheduled_team_games,
)
from apollo.draft.projections import ProjectionError


def _history(starts: tuple[float, float, float]):
    return (
        (20222023, {"gamesStarted": starts[0]}),
        (20212022, {"gamesStarted": starts[1]}),
        (20202021, {"gamesStarted": starts[2]}),
    )


def test_goalie_workload_schedule_normalizes_2020_21_shortened_season():
    assert scheduled_team_games(20202021) == 56.0
    assert scheduled_team_games(20212022) == 82.0
    with pytest.raises(ProjectionError, match="undefined"):
        scheduled_team_games(20192020)


def test_schedule_normalized_workload_preserves_equal_start_share():
    history = _history((41.0, 41.0, 28.0))
    assert project_workload_starts(history, (0.60, 0.30, 0.10)) == pytest.approx(41.0)


def test_more_recent_workload_weights_move_toward_latest_role():
    history = _history((61.5, 41.0, 14.0))
    assert project_workload_starts(history, (0.60, 0.30, 0.10)) == pytest.approx(51.25)
    assert project_workload_starts(history, (0.70, 0.20, 0.10)) == pytest.approx(53.30)
    assert project_workload_starts(history, (0.80, 0.15, 0.05)) == pytest.approx(56.375)


def test_workload_candidate_changes_only_starts_and_total_stats():
    baseline = GoalieBacktestPlayer(
        player_id=1,
        player_name="Goalie A",
        projected_starts=40.0,
        actual_starts=50.0,
        projected_stats={
            "wins": 20.0,
            "saves": 1200.0,
            "goalsAgainst": 100.0,
            "shutouts": 4.0,
            "savePctg": 0.920,
            "goalsAgainstAvg": 2.50,
        },
        actual_stats={
            "wins": 25.0,
            "saves": 1500.0,
            "goalsAgainst": 125.0,
            "shutouts": 5.0,
            "savePctg": 0.920,
            "goalsAgainstAvg": 2.50,
        },
    )
    candidate = apply_workload_to_baseline(baseline, 50.0)

    assert candidate.projected_starts == pytest.approx(50.0)
    assert candidate.projected_stats["wins"] == pytest.approx(25.0)
    assert candidate.projected_stats["saves"] == pytest.approx(1500.0)
    assert candidate.projected_stats["goalsAgainst"] == pytest.approx(125.0)
    assert candidate.projected_stats["shutouts"] == pytest.approx(5.0)
    assert candidate.projected_stats["savePctg"] == pytest.approx(0.920)
    assert candidate.projected_stats["goalsAgainstAvg"] == pytest.approx(2.50)


def test_goalie_workload_candidate_cli_contract():
    args = cli_v44.build_parser().parse_args(
        ["draft", "goalie-workload-candidate-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-workload-candidate-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

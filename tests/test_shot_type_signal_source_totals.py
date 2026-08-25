import pytest

from apollo.draft.shot_type_signal_backtest import signal_value
from apollo.services.shot_type_signal_backtest import _shot_type_source_stats


def test_shot_type_source_stats_falls_back_to_canonical_season_totals():
    stats = {
        "shots": 200.0,
        "goals": 20.0,
        "shotsOnNetWrist": 100.0,
        "goalsWrist": 8.0,
    }

    normalized = _shot_type_source_stats(stats)

    assert normalized["shotTypeShots"] == pytest.approx(200.0)
    assert normalized["shotTypeGoals"] == pytest.approx(20.0)
    assert signal_value("wrist_shot_share", normalized) == pytest.approx(0.50)
    assert signal_value("wrist_goal_share", normalized) == pytest.approx(0.40)


def test_shot_type_source_stats_preserves_namespaced_report_totals_when_present():
    stats = {
        "shots": 200.0,
        "goals": 20.0,
        "shotTypeShots": 190.0,
        "shotTypeGoals": 19.0,
    }

    normalized = _shot_type_source_stats(stats)

    assert normalized["shotTypeShots"] == pytest.approx(190.0)
    assert normalized["shotTypeGoals"] == pytest.approx(19.0)

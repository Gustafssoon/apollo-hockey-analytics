import pytest

from apollo.draft.projections import ProjectionSeason, build_skater_projection
from apollo.draft.regression import (
    REGRESSION_MODEL_VERSION,
    REGRESSION_PSEUDO_GAMES_BY_STAT,
    build_position_priors,
    regress_rate,
)


def _history() -> tuple[ProjectionSeason, ...]:
    return tuple(
        ProjectionSeason(
            season=season,
            games_played=80.0,
            stats={
                "goals": 80.0,
                "assists": 80.0,
                "powerPlayPoints": 80.0,
                "shots": 80.0,
                "hits": 80.0,
                "blockedShots": 80.0,
            },
        )
        for season in (20252026, 20242025, 20232024)
    )


def test_production_regression_mapping_is_points_robust():
    assert REGRESSION_PSEUDO_GAMES_BY_STAT == {
        "goals": 5.0,
        "assists": 5.0,
        "powerPlayPoints": 0.0,
        "shots": 5.0,
        "hits": 0.0,
        "blockedShots": 10.0,
    }


def test_regress_rate_falls_back_to_raw_rate_without_prior():
    rate, applied = regress_rate(
        value=40.0,
        games_played=80.0,
        prior_rate=None,
        pseudo_games=10.0,
    )

    assert rate == pytest.approx(0.5)
    assert applied is False


def test_projection_applies_regression_only_to_mapped_stats():
    history = _history()
    priors = {
        (season.season, "F", stat_name): 0.0
        for season in history
        for stat_name in REGRESSION_PSEUDO_GAMES_BY_STAT
    }
    baseline = build_skater_projection(
        player_id=1,
        player_name="Baseline Player",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
    )
    regressed = build_skater_projection(
        player_id=1,
        player_name="Regressed Player",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
        regression_priors=priors,
    )

    assert regressed.stats["goals"] < baseline.stats["goals"]
    assert regressed.stats["assists"] < baseline.stats["assists"]
    assert regressed.stats["shots"] < baseline.stats["shots"]
    assert regressed.stats["blockedShots"] < baseline.stats["blockedShots"]
    assert regressed.stats["powerPlayPoints"] == baseline.stats["powerPlayPoints"]
    assert regressed.stats["hits"] == baseline.stats["hits"]
    assert regressed.regression_model_version == REGRESSION_MODEL_VERSION


def test_position_priors_are_gp_weighted_and_split_by_position_group():
    priors = build_position_priors(
        (
            (20252026, "C", 80.0, {"goals": 40.0}),
            (20252026, "L", 40.0, {"goals": 10.0}),
            (20252026, "D", 80.0, {"goals": 8.0}),
        )
    )

    assert priors[(20252026, "F", "goals")] == pytest.approx(50.0 / 120.0)
    assert priors[(20252026, "D", "goals")] == pytest.approx(0.1)

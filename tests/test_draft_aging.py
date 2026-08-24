from datetime import date

import pytest

from apollo.draft.aging import (
    AGE_MODEL_VERSION,
    PRODUCTION_AGE_CURVE,
    adjust_rate_between_ages,
    adjust_rate_for_seasons,
    season_reference_date,
)


def test_production_age_model_is_medium_curve():
    assert AGE_MODEL_VERSION == "apollo-age-medium-v0.1"
    assert PRODUCTION_AGE_CURVE.name == "medium"
    assert PRODUCTION_AGE_CURVE.forward_peak_age == pytest.approx(27.5)
    assert PRODUCTION_AGE_CURVE.defense_peak_age == pytest.approx(28.5)
    assert PRODUCTION_AGE_CURVE.pre_peak_slope == pytest.approx(0.015)
    assert PRODUCTION_AGE_CURVE.post_peak_slope == pytest.approx(0.020)


def test_medium_curve_raises_pre_peak_rate_and_lowers_post_peak_rate():
    younger = adjust_rate_between_ages(
        observed_rate=1.0,
        source_age=25.0,
        target_age=26.0,
        position="C",
        curve=PRODUCTION_AGE_CURVE,
    )
    older = adjust_rate_between_ages(
        observed_rate=1.0,
        source_age=29.0,
        target_age=30.0,
        position="C",
        curve=PRODUCTION_AGE_CURVE,
    )

    assert younger > 1.0
    assert older < 1.0


def test_age_adjustment_uses_october_first_season_reference():
    birth_date = date(1997, 1, 13)

    assert season_reference_date(20262027) == date(2026, 10, 1)
    adjusted = adjust_rate_for_seasons(
        observed_rate=1.0,
        birth_date=birth_date,
        source_season=20252026,
        target_season=20262027,
        position="C",
    )
    assert adjusted < 1.0

    with pytest.raises(ValueError, match="Invalid NHL season id"):
        season_reference_date(20262028)

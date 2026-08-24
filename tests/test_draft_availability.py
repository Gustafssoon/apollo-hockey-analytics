import pytest

from apollo.draft.availability import (
    AVAILABILITY_MODEL_VERSION,
    AvailabilityError,
    normalize_games_to_82,
    project_available_games,
    regular_season_game_limit,
)


def test_availability_model_version_is_explicit():
    assert AVAILABILITY_MODEL_VERSION == "apollo-availability-shrink50-v0.1"


def test_project_available_games_uses_shrink50_to_full_season():
    projected = project_available_games(
        (
            (20252026, 80.0),
            (20242025, 70.0),
            (20232024, 60.0),
        ),
        (0.6, 0.3, 0.1),
    )

    assert projected == pytest.approx(78.5)


def test_project_available_games_preserves_calendar_weights_when_latest_is_missing():
    projected = project_available_games(
        (
            (20252026, 0.0),
            (20242025, 70.0),
            (20232024, 60.0),
        ),
        (0.6, 0.3, 0.1),
    )

    assert projected == pytest.approx(74.75)


def test_shortened_season_is_normalized_as_availability_not_raw_games():
    assert regular_season_game_limit(20202021) == 56
    assert normalize_games_to_82(20202021, 56.0) == pytest.approx(82.0)
    assert normalize_games_to_82(20202021, 42.0) == pytest.approx(61.5)

    projected = project_available_games(
        (
            (20222023, 82.0),
            (20212022, 82.0),
            (20202021, 56.0),
        ),
        (0.6, 0.3, 0.1),
    )
    assert projected == pytest.approx(82.0)


def test_availability_model_rejects_unsupported_old_season():
    with pytest.raises(AvailabilityError, match="does not support NHL season"):
        normalize_games_to_82(20192020, 70.0)

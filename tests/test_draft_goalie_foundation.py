import pytest

from apollo import cli_v42
from apollo.db import Database
from apollo.draft.goalie_foundation import (
    GOALIE_CORE_SOURCE_FIELDS,
    GOALIE_FOUNDATION_FIELDS,
    build_goalie_foundation_audit,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_foundation import run_goalie_foundation_audit


def _complete_goalie_season(*, starts: float = 20.0, shot_delta: float = 0.0):
    saves = 550.0
    goals_against = 50.0
    return {
        "gamesPlayed": starts + 2.0,
        "gamesStarted": starts,
        "wins": starts * 0.55,
        "losses": starts * 0.30,
        "otLosses": starts * 0.15,
        "shotsAgainst": saves + goals_against + shot_delta,
        "saves": saves,
        "goalsAgainst": goals_against,
        "savePctg": saves / (saves + goals_against),
        "goalsAgainstAvg": 2.5,
        "shutouts": 2.0,
        "timeOnIce": starts * 3600.0,
    }


def test_goalie_foundation_fields_cover_workload_categories_and_ratios():
    assert GOALIE_CORE_SOURCE_FIELDS == (
        "gamesStarted",
        "wins",
        "shotsAgainst",
        "saves",
        "goalsAgainst",
        "shutouts",
    )
    assert "gamesPlayed" in GOALIE_FOUNDATION_FIELDS
    assert "savePctg" in GOALIE_FOUNDATION_FIELDS
    assert "goalsAgainstAvg" in GOALIE_FOUNDATION_FIELDS
    assert "timeOnIce" in GOALIE_FOUNDATION_FIELDS


def test_goalie_season_coverage_counts_core_fields_and_shot_identity():
    complete = _complete_goalie_season()
    mismatch = _complete_goalie_season(starts=8.0, shot_delta=1.0)
    no_starts = {"gamesPlayed": 5.0, "gamesStarted": 0.0}
    stats_by_player = {
        1: {20252026: complete},
        2: {20252026: mismatch},
        3: {20252026: no_starts},
    }

    result = build_goalie_foundation_audit(stats_by_player, 20252026, years=1)
    coverage = result.season_coverage[0]

    assert coverage.season == 20252026
    assert coverage.goalies_with_games == 3
    assert coverage.goalies_with_starts == 2
    assert coverage.complete_core == 2
    assert coverage.shot_identity_checked == 2
    assert coverage.shot_identity_exact == 1
    counts = dict(coverage.field_counts)
    assert counts["gamesStarted"] == 3
    assert counts["wins"] == 2
    assert counts["savePctg"] == 2


def test_goalie_history_coverage_uses_previous_seasons_only():
    target = 20252026
    stats_by_player = {
        1: {
            target: _complete_goalie_season(starts=25.0),
            20242025: _complete_goalie_season(),
            20232024: _complete_goalie_season(),
            20222023: _complete_goalie_season(),
        },
        2: {
            target: _complete_goalie_season(starts=25.0),
            20242025: _complete_goalie_season(),
            20232024: _complete_goalie_season(),
        },
        3: {
            target: _complete_goalie_season(starts=25.0),
        },
    }

    result = build_goalie_foundation_audit(stats_by_player, target, years=1)
    coverage = next(item for item in result.history_coverage if item.min_actual_starts == 20)

    assert result.target_seasons == (20252026,)
    assert result.data_seasons == (20252026, 20242025, 20232024, 20222023)
    assert coverage.actual_eligible == 3
    assert coverage.at_least_one_source == 2
    assert coverage.at_least_two_sources == 2
    assert coverage.three_sources == 1


def test_goalie_foundation_service_filters_skater_and_playoff_rows(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    with database.connect() as connection:
        goalie = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position) VALUES ('Goalie', 'One', 'G')"
        ).lastrowid
        skater = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position) VALUES ('Skater', 'One', 'C')"
        ).lastrowid
        connection.executemany(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            ((goalie, "9000001"), (skater, "9000002")),
        )
        rows = []
        for player_id, game_type in ((goalie, 2), (goalie, 3), (skater, 2)):
            for name, value in _complete_goalie_season().items():
                rows.append((player_id, 20252026, game_type, name, value))
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    result = run_goalie_foundation_audit(database, 20252026, years=1)

    assert result.season_coverage[0].goalies_with_games == 1
    assert result.season_coverage[0].complete_core == 1


def test_goalie_foundation_cli_and_validation_contract():
    args = cli_v42.build_parser().parse_args(
        ["draft", "goalie-foundation-summary", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "goalie-foundation-summary"
    assert args.years == 3

    with pytest.raises(ProjectionError, match="years must be >= 1"):
        build_goalie_foundation_audit({}, 20252026, years=0)

import pytest

from apollo import cli_v47
from apollo.db import Database
from apollo.draft.goalie_age_candidate_gate import (
    GOALIE_AGE_GATE_COHORTS,
    GOALIE_AGE_GATE_SLOPE,
)
from apollo.services.goalie_age_candidate_gate import run_goalie_age_candidate_gate


def _insert_goalie(database: Database, name: str, birth_date: str) -> int:
    first, last = name.split(" ", 1)
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position, nhl_team) "
            "VALUES (?, ?, 'G', 'EDM')",
            (first, last),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) "
            "VALUES (?, 'nhl', ?)",
            (player_id, str(8700000 + player_id)),
        )
        connection.execute(
            """
            INSERT INTO nhl_player_profile
                (player_id, is_active, team_abbrev, position, birth_date, fetched_at)
            VALUES (?, 1, 'EDM', 'G', ?, '2026-08-25T00:00:00Z')
            """,
            (player_id, birth_date),
        )
    return player_id


def _insert_goalie_season(
    database: Database,
    player_id: int,
    season: int,
    starts: float,
) -> None:
    stats = {
        "gamesStarted": starts,
        "wins": starts * 0.5,
        "saves": starts * 30.0,
        "goalsAgainst": starts * 2.5,
        "shutouts": starts * 0.08,
        "savePctg": 0.920,
        "goalsAgainstAvg": 2.50,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO nhl_player_season_stat
                (player_id, season, game_type, stat_name, value)
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def _build_six_season_fixture(tmp_path) -> Database:
    database = Database(tmp_path / "apollo.db")
    young = _insert_goalie(database, "Young Goalie", "2000-01-01")
    old = _insert_goalie(database, "Old Goalie", "1990-01-01")
    seasons = (20252026, 20242025, 20232024, 20222023, 20212022, 20202021)
    for season in seasons:
        _insert_goalie_season(database, young, season, 36.0)
        _insert_goalie_season(database, old, season, 42.0)
    return database


def test_goalie_age_gate_contract_is_locked():
    assert GOALIE_AGE_GATE_SLOPE == pytest.approx(0.005)
    assert tuple(cohort.name for cohort in GOALIE_AGE_GATE_COHORTS) == (
        "GS10 ALL",
        "GS20 ALL",
        "GS30 ALL",
        "GS20 AGE<30",
        "GS20 AGE>=30",
    )
    assert tuple(cohort.min_actual_starts for cohort in GOALIE_AGE_GATE_COHORTS) == (
        10,
        20,
        30,
        20,
        20,
    )


def test_goalie_age_gate_keeps_age_subgroups_separate(tmp_path):
    result = run_goalie_age_candidate_gate(
        _build_six_season_fixture(tmp_path),
        20252026,
        years=3,
    )
    by_name = {cohort.cohort.name: cohort for cohort in result.cohorts}

    assert by_name["GS20 ALL"].player_seasons == 6
    assert by_name["GS20 AGE<30"].player_seasons == 3
    assert by_name["GS20 AGE>=30"].player_seasons == 3
    assert by_name["GS20 ALL"].applied == 6
    assert by_name["GS20 AGE<30"].applied == 3
    assert by_name["GS20 AGE>=30"].applied == 3


def test_goalie_age_gate_rate_stats_remain_exact(tmp_path):
    result = run_goalie_age_candidate_gate(
        _build_six_season_fixture(tmp_path),
        20252026,
        years=3,
    )
    for cohort in result.cohorts:
        baseline = {metric.stat_name: metric for metric in cohort.baseline_metrics}
        candidate = {metric.stat_name: metric for metric in cohort.candidate_metrics}
        for stat_name in ("savePctg", "goalsAgainstAvg"):
            assert candidate[stat_name].mae == baseline[stat_name].mae
            assert candidate[stat_name].spearman_rho == baseline[stat_name].spearman_rho


def test_goalie_age_gate_primary_matches_approved_candidate(tmp_path):
    database = _build_six_season_fixture(tmp_path)
    result = run_goalie_age_candidate_gate(database, 20252026, years=3)

    assert cli_v47._approved_equivalence(database, 20252026, 3, result)


def test_goalie_age_gate_cli_contract():
    args = cli_v47.build_parser().parse_args(
        ["draft", "goalie-age-candidate-gate", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-age-candidate-gate"
    assert args.years == 3

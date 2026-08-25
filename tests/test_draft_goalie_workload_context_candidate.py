import pytest

from apollo import cli_v46
from apollo.db import Database
from apollo.draft.goalie_baseline import GoalieBacktestPlayer
from apollo.draft.goalie_workload_context_candidate import (
    AGE_SLOPES,
    GOALIE_WORKLOAD_CONTEXT_VARIANTS,
    LATEST_SHARE_STRENGTHS,
    age_factor,
    apply_context_factor,
    latest_share_factor,
)
from apollo.services.goalie_workload_context_candidate import (
    run_goalie_workload_context_candidate_backtest,
)


def test_latest_share_factor_is_locked_mean_reversion_with_clamp():
    assert latest_share_factor(0.8, 0.4, 0.10) == pytest.approx(0.90)
    assert latest_share_factor(0.2, 0.4, 0.10) == pytest.approx(1.05)
    assert latest_share_factor(2.0, 0.2, 0.20) == pytest.approx(0.80)


def test_age_factor_is_locked_per_year_adjustment_with_clamp():
    assert age_factor(34.0, 30.0, 0.01) == pytest.approx(0.96)
    assert age_factor(26.0, 30.0, 0.01) == pytest.approx(1.04)
    assert age_factor(50.0, 30.0, 0.02) == pytest.approx(0.80)


def test_context_factor_changes_workload_totals_only():
    baseline = GoalieBacktestPlayer(
        player_id=1,
        player_name="Goalie One",
        projected_starts=40.0,
        actual_starts=45.0,
        projected_stats={
            "wins": 20.0,
            "saves": 1200.0,
            "goalsAgainst": 100.0,
            "shutouts": 4.0,
            "savePctg": 0.920,
            "goalsAgainstAvg": 2.50,
        },
        actual_stats={
            "wins": 22.0,
            "saves": 1300.0,
            "goalsAgainst": 110.0,
            "shutouts": 5.0,
            "savePctg": 0.918,
            "goalsAgainstAvg": 2.60,
        },
    )

    candidate = apply_context_factor(baseline, 0.90)

    assert candidate.projected_starts == pytest.approx(36.0)
    assert candidate.projected_stats["wins"] == pytest.approx(18.0)
    assert candidate.projected_stats["saves"] == pytest.approx(1080.0)
    assert candidate.projected_stats["goalsAgainst"] == pytest.approx(90.0)
    assert candidate.projected_stats["shutouts"] == pytest.approx(3.6)
    assert candidate.projected_stats["savePctg"] == baseline.projected_stats["savePctg"]
    assert candidate.projected_stats["goalsAgainstAvg"] == baseline.projected_stats["goalsAgainstAvg"]


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
            (player_id, str(8800000 + player_id)),
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


def test_context_priors_are_source_only_when_target_workload_changes(tmp_path):
    database = Database(tmp_path / "apollo.db")
    evaluated = _insert_goalie(database, "Evaluated Goalie", "1996-01-01")
    source_peer = _insert_goalie(database, "Source Peer", "1990-01-01")

    for season, starts in ((20242025, 40.0), (20232024, 35.0), (20222023, 30.0)):
        _insert_goalie_season(database, evaluated, season, starts)
    _insert_goalie_season(database, source_peer, 20242025, 20.0)
    _insert_goalie_season(database, evaluated, 20252026, 30.0)

    first = run_goalie_workload_context_candidate_backtest(database, 20252026)
    _insert_goalie_season(database, evaluated, 20252026, 80.0)
    second = run_goalie_workload_context_candidate_backtest(database, 20252026)

    assert first.latest_share_prior == pytest.approx(30.0 / 82.0)
    assert second.latest_share_prior == pytest.approx(first.latest_share_prior)
    assert second.age_prior == pytest.approx(first.age_prior)


def test_context_candidate_variants_and_cli_contract():
    assert LATEST_SHARE_STRENGTHS == (0.05, 0.10, 0.20)
    assert AGE_SLOPES == (0.005, 0.010, 0.020)
    assert len(GOALIE_WORKLOAD_CONTEXT_VARIANTS) == 6
    assert {spec.signal for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS} == {"latest_share", "age"}

    args = cli_v46.build_parser().parse_args(
        ["draft", "goalie-workload-context-candidate-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-workload-context-candidate-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

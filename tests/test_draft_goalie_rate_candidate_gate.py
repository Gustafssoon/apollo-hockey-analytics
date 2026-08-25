import pytest

from apollo import cli_v50
from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GoalieBacktestMetric,
    GoalieBacktestResult,
)
from apollo.draft.goalie_rate_candidate_gate import (
    GOALIE_RATE_GATE_CANDIDATES,
    GOALIE_RATE_GATE_COHORTS,
    GOALIE_RATE_GATE_STRENGTH,
    GoalieRateGateSeasonCandidate,
    GoalieRateGateSeasonResult,
    build_goalie_rate_gate_aggregate,
)
from apollo.services import goalie_rate_candidate_gate as gate_service
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_aggregate
from apollo.services.goalie_rate_candidate_gate import run_goalie_rate_candidate_gate


def _result(
    season: int,
    n: int,
    *,
    save_pct_mae: float,
    gaa_mae: float,
) -> GoalieBacktestResult:
    metrics = tuple(
        GoalieBacktestMetric(
            stat_name=stat_name,
            mae=(
                save_pct_mae
                if stat_name == "savePctg"
                else gaa_mae
                if stat_name == "goalsAgainstAvg"
                else 1.0
            ),
            spearman_rho=0.5,
            oracle_starts_mae=None,
            oracle_starts_spearman_rho=None,
        )
        for stat_name in GOALIE_BACKTEST_STATS
    )
    return GoalieBacktestResult(
        target_season=season,
        source_seasons=(season - 10001, season - 20002, season - 30003),
        evaluated_goalies=n,
        actual_eligible_goalies=n,
        coverage=1.0,
        metrics=metrics,
    )


def test_rate_gate_grid_is_locked_to_5_percent_single_category_candidates():
    assert GOALIE_RATE_GATE_STRENGTH == pytest.approx(0.05)
    assert GOALIE_RATE_GATE_CANDIDATES == ("sv-5", "gaa-5")
    assert tuple(cohort.name for cohort in GOALIE_RATE_GATE_COHORTS) == (
        "GS10 ALL",
        "GS20 ALL",
        "GS30 ALL",
        "GS20 AGE<30",
        "GS20 AGE>=30",
    )


def test_rate_gate_aggregate_tracks_three_year_gain_and_worst_year():
    season_results = []
    seasons = (20252026, 20242025, 20232024)
    for season_index, season in enumerate(seasons):
        for cohort in GOALIE_RATE_GATE_COHORTS:
            baseline = _result(season, 4, save_pct_mae=0.012, gaa_mae=0.360)
            sv_gain = (0.0003, 0.0002, 0.0001)[season_index]
            gaa_gain = (0.010, 0.008, 0.004)[season_index]
            season_results.append(
                GoalieRateGateSeasonResult(
                    cohort=cohort,
                    target_season=season,
                    baseline=baseline,
                    candidates=(
                        GoalieRateGateSeasonCandidate(
                            "sv-5",
                            "savePctg",
                            _result(
                                season,
                                4,
                                save_pct_mae=0.012 - sv_gain,
                                gaa_mae=0.360,
                            ),
                        ),
                        GoalieRateGateSeasonCandidate(
                            "gaa-5",
                            "goalsAgainstAvg",
                            _result(
                                season,
                                4,
                                save_pct_mae=0.012,
                                gaa_mae=0.360 - gaa_gain,
                            ),
                        ),
                    ),
                )
            )

    aggregate = build_goalie_rate_gate_aggregate(tuple(season_results))
    sv = next(
        row
        for row in aggregate.rows
        if row.cohort.name == "GS20 ALL" and row.candidate_name == "sv-5"
    )
    gaa = next(
        row
        for row in aggregate.rows
        if row.cohort.name == "GS20 ALL" and row.candidate_name == "gaa-5"
    )

    assert sv.improved_years == 3
    assert sv.worst_mae_gain == pytest.approx(0.0001)
    assert gaa.improved_years == 3
    assert gaa.worst_mae_gain == pytest.approx(0.004)


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
            (player_id, str(9000000 + player_id)),
        )
        connection.execute(
            """
            INSERT INTO nhl_player_profile
                (player_id, is_active, team_abbrev, position, birth_date, fetched_at)
            VALUES (?, 1, 'EDM', 'G', ?, '2026-08-26T00:00:00Z')
            """,
            (player_id, birth_date),
        )
    return player_id


def _insert_goalie_season(
    database: Database,
    player_id: int,
    season: int,
    starts: float,
    *,
    save_pct: float,
    gaa: float,
) -> None:
    shots = starts * 32.0
    saves = shots * save_pct
    goals_against = shots - saves
    stats = {
        "gamesStarted": starts,
        "wins": starts * 0.5,
        "saves": saves,
        "goalsAgainst": goals_against,
        "shutouts": starts * 0.08,
        "savePctg": save_pct,
        "goalsAgainstAvg": gaa,
        "timeOnIce": starts * 60.0,
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


def _build_fixture(database: Database) -> None:
    younger = _insert_goalie(database, "Young Goalie", "1998-01-01")
    older = _insert_goalie(database, "Older Goalie", "1990-01-01")
    source_seasons = (20242025, 20232024, 20222023)
    for season in source_seasons:
        _insert_goalie_season(database, younger, season, 35.0, save_pct=0.920, gaa=2.50)
        _insert_goalie_season(database, older, season, 30.0, save_pct=0.900, gaa=3.00)
    _insert_goalie_season(database, younger, 20252026, 40.0, save_pct=0.915, gaa=2.60)
    _insert_goalie_season(database, older, 20252026, 32.0, save_pct=0.905, gaa=2.90)


def test_rate_gate_builds_source_priors_once_before_subgroup_results(tmp_path, monkeypatch):
    database = Database(tmp_path / "apollo.db")
    _build_fixture(database)
    original = gate_service._build_source_priors
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_service, "_build_source_priors", counted)
    gate_service.run_goalie_rate_candidate_gate_season(database, 20252026)

    assert calls == 1


def test_gs20_all_gate_is_exactly_equivalent_to_approved_shootout(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _build_fixture(database)

    gate = run_goalie_rate_candidate_gate(database, 20252026, years=1)
    shootout = run_goalie_rate_candidate_aggregate(database, 20252026, years=1)

    for candidate_name in GOALIE_RATE_GATE_CANDIDATES:
        row = next(
            item
            for item in gate.rows
            if item.cohort.name == "GS20 ALL" and item.candidate_name == candidate_name
        )
        approved = next(
            item for item in shootout.variants if item.spec.name == candidate_name
        )
        assert row.player_seasons == approved.player_seasons
        assert row.candidate_metrics == approved.metrics


def test_goalie_rate_candidate_gate_cli_contract():
    args = cli_v50.build_parser().parse_args(
        ["draft", "goalie-rate-candidate-gate", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-rate-candidate-gate"
    assert args.years == 3

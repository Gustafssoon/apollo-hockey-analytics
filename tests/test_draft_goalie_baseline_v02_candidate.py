import pytest

from apollo import cli_v51
from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GoalieBacktestMetric,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
)
from apollo.draft.goalie_baseline_v02_candidate import (
    GOALIE_BASELINE_V02_CANDIDATE_VERSION,
    GOALIE_BASELINE_V02_COMPONENTS,
    GoalieBaselineV02SeasonResult,
    build_goalie_baseline_v02_aggregate,
    build_goalie_baseline_v02_player,
)
from apollo.services.goalie_baseline_v02_candidate import (
    run_goalie_baseline_v02_candidate_aggregate,
)
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_aggregate


def test_goalie_baseline_v02_components_are_locked():
    assert (
        GOALIE_BASELINE_V02_CANDIDATE_VERSION
        == "apollo-goalie-baseline-v0.2-candidate"
    )
    assert GOALIE_BASELINE_V02_COMPONENTS == ("sv-5", "gaa-5")


def test_goalie_baseline_v02_player_changes_only_sv_pct_and_gaa():
    baseline = GoalieBacktestPlayer(
        player_id=1,
        player_name="Goalie One",
        projected_starts=40.0,
        actual_starts=42.0,
        projected_stats={
            "wins": 20.0,
            "saves": 1200.0,
            "goalsAgainst": 100.0,
            "shutouts": 4.0,
            "savePctg": 0.930,
            "goalsAgainstAvg": 3.00,
        },
        actual_stats={
            "wins": 21.0,
            "saves": 1250.0,
            "goalsAgainst": 105.0,
            "shutouts": 4.0,
            "savePctg": 0.920,
            "goalsAgainstAvg": 2.80,
        },
    )

    candidate = build_goalie_baseline_v02_player(
        baseline,
        save_pct_prior=0.910,
        gaa_prior=2.50,
    )

    assert candidate.projected_starts == baseline.projected_starts
    assert candidate.projected_stats["savePctg"] == pytest.approx(0.929)
    assert candidate.projected_stats["goalsAgainstAvg"] == pytest.approx(2.975)
    for stat_name in ("wins", "saves", "goalsAgainst", "shutouts"):
        assert candidate.projected_stats[stat_name] == baseline.projected_stats[stat_name]


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


def test_goalie_baseline_v02_aggregate_integrates_both_rate_gains_only():
    results = tuple(
        GoalieBaselineV02SeasonResult(
            target_season=season,
            baseline=_result(season, 4, save_pct_mae=0.012, gaa_mae=0.360),
            candidate=_result(season, 4, save_pct_mae=0.0118, gaa_mae=0.352),
            save_pct_prior=0.910,
            gaa_prior=2.70,
        )
        for season in (20252026, 20242025, 20232024)
    )

    aggregate = build_goalie_baseline_v02_aggregate(results)
    baseline = {metric.stat_name: metric for metric in aggregate.baseline_metrics}
    candidate = {metric.stat_name: metric for metric in aggregate.candidate_metrics}

    assert aggregate.player_seasons == 12
    sv_gain = baseline["savePctg"].mae - candidate["savePctg"].mae
    gaa_gain = baseline["goalsAgainstAvg"].mae - candidate["goalsAgainstAvg"].mae
    assert sv_gain == pytest.approx(0.0002)
    assert gaa_gain == pytest.approx(0.008)
    for stat_name in ("gamesStarted", "wins", "saves", "goalsAgainst", "shutouts"):
        assert candidate[stat_name] == baseline[stat_name]


def _insert_goalie(database: Database, name: str) -> int:
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
            (player_id, str(9100000 + player_id)),
        )
    return player_id


def _insert_goalie_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    starts: float,
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


def test_goalie_baseline_v02_components_match_approved_single_candidates(tmp_path):
    database = Database(tmp_path / "apollo.db")
    goalie_one = _insert_goalie(database, "Goalie One")
    goalie_two = _insert_goalie(database, "Goalie Two")
    for season in (20242025, 20232024, 20222023):
        _insert_goalie_season(
            database,
            goalie_one,
            season,
            starts=35.0,
            save_pct=0.920,
            gaa=2.50,
        )
        _insert_goalie_season(
            database,
            goalie_two,
            season,
            starts=30.0,
            save_pct=0.900,
            gaa=3.00,
        )
    _insert_goalie_season(
        database,
        goalie_one,
        20252026,
        starts=40.0,
        save_pct=0.915,
        gaa=2.60,
    )
    _insert_goalie_season(
        database,
        goalie_two,
        20252026,
        starts=32.0,
        save_pct=0.905,
        gaa=2.90,
    )

    integrated = run_goalie_baseline_v02_candidate_aggregate(
        database,
        20252026,
        years=1,
    )
    shootout = run_goalie_rate_candidate_aggregate(database, 20252026, years=1)
    metrics = {metric.stat_name: metric for metric in integrated.candidate_metrics}
    sv = next(item for item in shootout.variants if item.spec.name == "sv-5")
    gaa = next(item for item in shootout.variants if item.spec.name == "gaa-5")
    sv_metrics = {metric.stat_name: metric for metric in sv.metrics}
    gaa_metrics = {metric.stat_name: metric for metric in gaa.metrics}

    assert metrics["savePctg"] == sv_metrics["savePctg"]
    assert metrics["goalsAgainstAvg"] == gaa_metrics["goalsAgainstAvg"]
    baseline = {metric.stat_name: metric for metric in integrated.baseline_metrics}
    for stat_name in ("gamesStarted", "wins", "saves", "goalsAgainst", "shutouts"):
        assert metrics[stat_name] == baseline[stat_name]


def test_goalie_baseline_v02_candidate_cli_contract():
    args = cli_v51.build_parser().parse_args(
        ["draft", "goalie-baseline-v02-candidate-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-baseline-v02-candidate-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

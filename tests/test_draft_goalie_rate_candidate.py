import pytest

from apollo import cli_v49
from apollo.db import Database
from apollo.draft.goalie_baseline import GoalieBacktestPlayer
from apollo.draft.goalie_rate_candidate import (
    GOALIE_RATE_STRENGTHS,
    GOALIE_RATE_VARIANTS,
    apply_rate_regression,
    regress_to_prior,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_backtest


def test_regress_to_prior_is_locked_linear_mean_reversion():
    assert regress_to_prior(0.930, 0.910, 0.05) == pytest.approx(0.929)
    assert regress_to_prior(3.00, 2.50, 0.20) == pytest.approx(2.90)
    with pytest.raises(ProjectionError):
        regress_to_prior(0.920, 0.910, 1.10)


def test_rate_candidate_changes_only_selected_ratio_stat():
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
            "goalsAgainstAvg": 2.50,
        },
        actual_stats={
            "wins": 21.0,
            "saves": 1250.0,
            "goalsAgainst": 105.0,
            "shutouts": 4.0,
            "savePctg": 0.920,
            "goalsAgainstAvg": 2.60,
        },
    )
    spec = next(item for item in GOALIE_RATE_VARIANTS if item.name == "sv-10")

    candidate = apply_rate_regression(baseline, spec, 0.910)

    assert candidate.projected_starts == baseline.projected_starts
    assert candidate.projected_stats["savePctg"] == pytest.approx(0.928)
    for stat_name in (
        "wins",
        "saves",
        "goalsAgainst",
        "shutouts",
        "goalsAgainstAvg",
    ):
        assert candidate.projected_stats[stat_name] == baseline.projected_stats[stat_name]


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
            (player_id, str(8900000 + player_id)),
        )
    return player_id


def _insert_goalie_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    saves: float,
    goals_against: float,
    save_pct: float,
    gaa: float,
    toi: float = 1000.0,
) -> None:
    stats = {
        "gamesStarted": 30.0,
        "wins": 15.0,
        "saves": saves,
        "goalsAgainst": goals_against,
        "shutouts": 3.0,
        "savePctg": save_pct,
        "goalsAgainstAvg": gaa,
        "timeOnIce": toi,
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


def test_rate_priors_are_source_only_when_target_rates_change(tmp_path):
    database = Database(tmp_path / "apollo.db")
    evaluated = _insert_goalie(database, "Evaluated Goalie")
    source_peer = _insert_goalie(database, "Source Peer")

    for season in (20242025, 20232024, 20222023):
        _insert_goalie_season(
            database,
            evaluated,
            season,
            saves=900.0,
            goals_against=100.0,
            save_pct=0.900,
            gaa=2.0,
        )
        _insert_goalie_season(
            database,
            source_peer,
            season,
            saves=800.0,
            goals_against=200.0,
            save_pct=0.800,
            gaa=4.0,
        )
    _insert_goalie_season(
        database,
        evaluated,
        20252026,
        saves=950.0,
        goals_against=100.0,
        save_pct=0.905,
        gaa=2.2,
    )

    first = run_goalie_rate_candidate_backtest(database, 20252026)
    _insert_goalie_season(
        database,
        evaluated,
        20252026,
        saves=500.0,
        goals_against=500.0,
        save_pct=0.500,
        gaa=8.0,
    )
    second = run_goalie_rate_candidate_backtest(database, 20252026)

    assert first.save_pct_prior == pytest.approx(0.85)
    assert first.gaa_prior == pytest.approx(3.0)
    assert second.save_pct_prior == pytest.approx(first.save_pct_prior)
    assert second.gaa_prior == pytest.approx(first.gaa_prior)


def test_rate_candidate_grid_is_locked_and_has_no_combinations():
    assert GOALIE_RATE_STRENGTHS == (0.05, 0.10, 0.20)
    assert len(GOALIE_RATE_VARIANTS) == 6
    assert {spec.stat_name for spec in GOALIE_RATE_VARIANTS} == {
        "savePctg",
        "goalsAgainstAvg",
    }
    assert all("+" not in spec.name for spec in GOALIE_RATE_VARIANTS)


def test_goalie_rate_candidate_cli_contract():
    args = cli_v49.build_parser().parse_args(
        ["draft", "goalie-rate-candidate-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-rate-candidate-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

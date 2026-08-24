from apollo import cli_v28
from apollo.db import Database
from apollo.draft.v05_candidate import (
    V05_CANDIDATE_MODEL_VERSION,
    V05_CANDIDATE_SHOOTING_STRENGTH,
)
from apollo.services.v05_candidate import run_v05_candidate_backtest


def _insert_player(database: Database, suffix: str) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Test', ?, 'C', 'EDM')
            """,
            (suffix,),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9700000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    goals: float,
    assists: float,
    shooting_pct: float | None,
) -> None:
    stats = {
        "gamesPlayed": 80.0,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": 20.0,
        "shots": 200.0,
        "hits": 80.0,
        "blockedShots": 40.0,
    }
    if shooting_pct is not None:
        stats["shootingPct5v5"] = shooting_pct
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def _seed(database: Database) -> None:
    hot = _insert_player(database, "Hot")
    cold = _insert_player(database, "Cold")
    fallback = _insert_player(database, "Fallback")

    for season in (20222023, 20232024, 20242025):
        _insert_season(
            database,
            hot,
            season,
            goals=40.0,
            assists=40.0,
            shooting_pct=0.12,
        )
        _insert_season(
            database,
            cold,
            season,
            goals=40.0,
            assists=40.0,
            shooting_pct=0.08,
        )
        _insert_season(
            database,
            fallback,
            season,
            goals=40.0,
            assists=40.0,
            shooting_pct=None,
        )

    _insert_season(
        database,
        hot,
        20252026,
        goals=38.0,
        assists=38.0,
        shooting_pct=None,
    )
    _insert_season(
        database,
        cold,
        20252026,
        goals=42.0,
        assists=42.0,
        shooting_pct=None,
    )
    _insert_season(
        database,
        fallback,
        20252026,
        goals=40.0,
        assists=40.0,
        shooting_pct=None,
    )


def _metric(result, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def test_v05_candidate_is_sh_offense_10():
    assert V05_CANDIDATE_MODEL_VERSION == "apollo-skater-v0.5-candidate-sh-offense10"
    assert V05_CANDIDATE_SHOOTING_STRENGTH == 0.10


def test_v05_candidate_service_keeps_full_sample_with_partial_sh_coverage(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_v05_candidate_backtest(database, 20252026)

    assert result.baseline.evaluated_players == 3
    assert result.candidate.evaluated_players == 3
    assert result.shooting_context_applied == 2
    assert result.candidate.model_version == V05_CANDIDATE_MODEL_VERSION


def test_v05_candidate_only_changes_goals_and_assists(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_v05_candidate_backtest(database, 20252026)

    for stat_name in ("gamesPlayed", "powerPlayPoints", "shots", "hits", "blockedShots"):
        baseline = _metric(result.baseline, stat_name)
        candidate = _metric(result.candidate, stat_name)
        assert candidate.mae == baseline.mae
        assert candidate.spearman_rho == baseline.spearman_rho


def test_v05_candidate_cli_reports_full_sample_fallback(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    cli_v28.main(
        [
            "draft",
            "v05-candidate-summary",
            "--season",
            "20252026",
            "--years",
            "1",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO SKATER v0.5 CANDIDATE FULL-SAMPLE GATE" in output
    assert "Shooting context applied: 2/3 player-seasons" in output
    assert "all others use exact v0.4 fallback" in output
    assert "Production remains apollo-skater-baseline-v0.4" in output

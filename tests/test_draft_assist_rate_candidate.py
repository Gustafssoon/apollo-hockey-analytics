from apollo import cli_v31
from apollo.db import Database
from apollo.draft.assist_rate_candidate import (
    ASSIST_RATE_CANDIDATE_MODEL_VERSIONS,
    ASSIST_RATE_CANDIDATE_STRENGTHS,
)
from apollo.services.assist_rate_candidate import run_assist_rate_candidate_backtest


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
            (player_id, str(9800000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    goals: float,
    assists: float,
    a60: float | None,
) -> None:
    stats = {
        "gamesPlayed": 80.0,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": 20.0,
        "shots": 200.0,
        "hits": 80.0,
        "blockedShots": 40.0,
        "timeOnIcePerGame5v5": 900.0,
    }
    if a60 is not None:
        stats["assistsPer605v5"] = a60
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
        _insert_season(database, hot, season, goals=35.0, assists=60.0, a60=2.0)
        _insert_season(database, cold, season, goals=35.0, assists=60.0, a60=1.0)
        _insert_season(database, fallback, season, goals=35.0, assists=60.0, a60=None)

    _insert_season(database, hot, 20252026, goals=35.0, assists=56.0, a60=None)
    _insert_season(database, cold, 20252026, goals=35.0, assists=64.0, a60=None)
    _insert_season(database, fallback, 20252026, goals=35.0, assists=60.0, a60=None)


def _metric(result, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _variant(result, strength: float):
    return next(variant for variant in result.variants if variant.strength == strength)


def test_assist_rate_candidate_constants_are_10_and_20_percent():
    assert ASSIST_RATE_CANDIDATE_STRENGTHS == (0.10, 0.20)
    assert ASSIST_RATE_CANDIDATE_MODEL_VERSIONS[0.10] == "apollo-skater-v0.6-candidate-a60-10"
    assert ASSIST_RATE_CANDIDATE_MODEL_VERSIONS[0.20] == "apollo-skater-v0.6-candidate-a60-20"


def test_assist_rate_candidate_keeps_full_sample_with_neutral_fallback(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_assist_rate_candidate_backtest(database, 20252026)

    assert result.baseline.evaluated_players == 3
    for strength in ASSIST_RATE_CANDIDATE_STRENGTHS:
        variant = _variant(result, strength)
        assert variant.result.evaluated_players == 3
        assert variant.applied == 2
        assert variant.result.model_version == ASSIST_RATE_CANDIDATE_MODEL_VERSIONS[strength]


def test_assist_rate_candidate_only_changes_assists_and_points(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_assist_rate_candidate_backtest(database, 20252026)

    for strength in ASSIST_RATE_CANDIDATE_STRENGTHS:
        candidate = _variant(result, strength).result
        for stat_name in (
            "gamesPlayed",
            "goals",
            "powerPlayPoints",
            "shots",
            "hits",
            "blockedShots",
        ):
            baseline_metric = _metric(result.baseline, stat_name)
            candidate_metric = _metric(candidate, stat_name)
            assert candidate_metric.mae == baseline_metric.mae
            assert candidate_metric.spearman_rho == baseline_metric.spearman_rho


def test_20_percent_candidate_applies_more_assist_mean_reversion(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_assist_rate_candidate_backtest(database, 20252026)
    assists10 = _metric(_variant(result, 0.10).result, "assists")
    assists20 = _metric(_variant(result, 0.20).result, "assists")

    assert assists10.mae != _metric(result.baseline, "assists").mae
    assert assists20.mae != assists10.mae


def test_assist_rate_candidate_cli_reports_fallback_and_untouched_categories(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    cli_v31.main(
        [
            "draft",
            "assist-rate-candidate-summary",
            "--season",
            "20252026",
            "--years",
            "1",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO ASSIST-RATE v0.6 CANDIDATE GATE" in output
    assert "Missing 3/3 A/60 context uses exact production v0.5 fallback" in output
    assert "2/3" in output
    assert "EXACT" in output
    assert "Production remains apollo-skater-baseline-v0.5" in output

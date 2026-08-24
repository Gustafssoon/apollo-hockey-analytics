from apollo import cli_v18
from apollo.db import Database
from apollo.draft.age_model_backtest import (
    AGE_MODEL_CANDIDATES,
    AgeModelBacktestPlayer,
    AgeModelHistorySeason,
    build_age_model_backtest_result,
)
from apollo.services.age_model_backtest import run_age_model_aggregate, run_age_model_backtest


def _insert_player(database: Database) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Test', 'Skater', 'C', 'EDM')
            """
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9200000 + player_id)),
        )
        connection.execute(
            """
            INSERT INTO nhl_player_profile (
                player_id, is_active, team_abbrev, position, sweater_number, birth_date, fetched_at
            ) VALUES (?, 1, 'EDM', 'C', 97, '1990-01-01', '2026-08-24T00:00:00Z')
            """,
            (player_id,),
        )
    return player_id


def _insert_season(database: Database, player_id: int, season: int, games: float, scale: float) -> None:
    stats = {
        "gamesPlayed": games,
        "goals": 30.0 * scale,
        "assists": 45.0 * scale,
        "powerPlayPoints": 25.0 * scale,
        "shots": 220.0 * scale,
        "hits": 90.0 * scale,
        "blockedShots": 50.0 * scale,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, stat_name, value) for stat_name, value in stats.items()],
        )


def _seed(database: Database) -> None:
    player_id = _insert_player(database)
    _insert_season(database, player_id, 20222023, 80, 1.00)
    _insert_season(database, player_id, 20232024, 80, 0.95)
    _insert_season(database, player_id, 20242025, 80, 0.90)
    _insert_season(database, player_id, 20252026, 80, 0.85)


def test_hybrid_candidate_uses_aggregate_stat_winners():
    hybrid = AGE_MODEL_CANDIDATES["hybrid"]

    assert hybrid["goals"] == "asymmetric"
    assert hybrid["assists"] == "medium"
    assert hybrid["powerPlayPoints"] == "asymmetric"
    assert hybrid["shots"] == "asymmetric"
    assert hybrid["hits"] == "asymmetric"
    assert hybrid["blockedShots"] == "asymmetric"


def test_age_model_candidates_can_help_declining_player():
    stats = {
        "goals": 24.0,
        "assists": 36.0,
        "powerPlayPoints": 20.0,
        "shots": 176.0,
        "hits": 72.0,
        "blockedShots": 40.0,
    }
    player = AgeModelBacktestPlayer(
        player_id=1,
        player_name="Older Player",
        position="C",
        projected_games=80.0,
        target_age=35.0,
        history=(
            AgeModelHistorySeason(34.0, 80.0, {key: value / 0.8 for key, value in stats.items()}),
            AgeModelHistorySeason(33.0, 80.0, {key: value / 0.7 for key, value in stats.items()}),
            AgeModelHistorySeason(32.0, 80.0, {key: value / 0.6 for key, value in stats.items()}),
        ),
        actual_stats=stats,
    )

    result = build_age_model_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        players=(player,),
    )
    candidates = {candidate.candidate_name: candidate for candidate in result.candidates}
    neutral_pts = next(metric for metric in candidates["neutral"].metrics if metric.stat_name == "points")
    medium_pts = next(metric for metric in candidates["medium_all"].metrics if metric.stat_name == "points")

    assert medium_pts.mae < neutral_pts.mae


def test_age_model_service_builds_all_candidates(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    result = run_age_model_backtest(database, 20252026)

    assert result.evaluated_players == 1
    assert result.source_seasons == (20242025, 20232024, 20222023)
    assert {candidate.candidate_name for candidate in result.candidates} == set(AGE_MODEL_CANDIDATES)


def test_age_model_summary_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed(database)

    aggregate = run_age_model_aggregate(database, 20252026, years=1)
    assert aggregate.total_player_seasons == 1

    cli_v18.main(
        [
            "draft",
            "age-model-summary",
            "--season",
            "20252026",
            "--years",
            "1",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO AGE MODEL CANDIDATE SHOOTOUT" in output
    assert "PTS is always derived from projected G + A" in output
    assert "asymmetric_all" in output
    assert "medium_all" in output
    assert "hybrid" in output
    assert "Raw-stat winners" in output

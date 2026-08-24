from apollo import cli_v23
from apollo.db import Database
from apollo.draft.projections import SKATER_PROJECTION_STATS
from apollo.draft.regression_backtest import (
    RegressionBacktestPlayer,
    RegressionHistorySeason,
    build_regression_backtest_result,
)
from apollo.draft.regression_model_backtest import (
    REGRESSION_MODEL_CANDIDATES,
    build_regression_model_aggregate,
)


def _stats(scale: float = 1.0) -> dict[str, float]:
    return {
        "goals": 20.0 * scale,
        "assists": 30.0 * scale,
        "powerPlayPoints": 10.0 * scale,
        "shots": 160.0 * scale,
        "hits": 80.0 * scale,
        "blockedShots": 40.0 * scale,
    }


def _backtest_result():
    seasons = (20242025, 20232024, 20222023)
    history = tuple(
        RegressionHistorySeason(season, 80.0, _stats(1.5)) for season in seasons
    )
    actual = {"gamesPlayed": 80.0, **_stats(1.0)}
    player = RegressionBacktestPlayer(
        player_id=1,
        player_name="Candidate Test",
        position="C",
        target_season=20252026,
        birth_date=None,
        projected_games=80.0,
        baseline_stats=_stats(1.5),
        history=history,
        actual_stats=actual,
    )
    priors = {
        (season, "F", stat): value / 80.0
        for season in seasons
        for stat, value in _stats(1.0).items()
    }
    return build_regression_backtest_result(
        target_season=20252026,
        source_seasons=seasons,
        players=(player,),
        priors=priors,
    )


def test_regression_model_candidate_mappings_are_complete():
    assert set(REGRESSION_MODEL_CANDIDATES) == {
        "baseline_v03",
        "all5",
        "all10",
        "category_robust",
        "category_bestmae",
        "points_robust",
        "points_bestmae",
    }
    for mapping in REGRESSION_MODEL_CANDIDATES.values():
        assert set(mapping) == set(SKATER_PROJECTION_STATS)
        assert mapping["goals"] == mapping["assists"]


def test_points_first_candidates_preserve_all5_points_metrics():
    aggregate = build_regression_model_aggregate((_backtest_result(),))
    candidates = {candidate.candidate_name: candidate for candidate in aggregate.candidates}

    def points(name: str):
        return next(metric for metric in candidates[name].metrics if metric.stat_name == "points")

    assert points("points_robust").weighted_mae == points("all5").weighted_mae
    assert points("points_bestmae").weighted_mae == points("all5").weighted_mae
    assert candidates["points_robust"].top25_overlap_rate == candidates["all5"].top25_overlap_rate


def test_robust_candidate_keeps_ppp_and_hits_at_v03():
    mapping = REGRESSION_MODEL_CANDIDATES["points_robust"]

    assert mapping["goals"] == "regress_pos_5"
    assert mapping["assists"] == "regress_pos_5"
    assert mapping["powerPlayPoints"] == "baseline_v03"
    assert mapping["shots"] == "regress_pos_5"
    assert mapping["hits"] == "baseline_v03"
    assert mapping["blockedShots"] == "regress_pos_10"


def _insert_player(database: Database, first_name: str, last_name: str) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, ?, 'C', 'EDM')
            """,
            (first_name, last_name),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9600000 + player_id)),
        )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    scale: float,
) -> None:
    stats = {"gamesPlayed": 80.0, **_stats(scale)}
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_regression_model_summary_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    alpha = _insert_player(database, "Alpha", "Candidate")
    beta = _insert_player(database, "Beta", "Candidate")
    for season in (20222023, 20232024, 20242025):
        _insert_season(database, alpha, season, 1.0)
        _insert_season(database, beta, season, 2.0)
    _insert_season(database, alpha, 20252026, 1.1)
    _insert_season(database, beta, 20252026, 1.8)

    cli_v23.main(
        [
            "draft",
            "regression-model-summary",
            "--season",
            "20252026",
            "--years",
            "1",
            "--db",
            str(database.path),
        ]
    )
    output = capsys.readouterr().out

    assert "APOLLO REGRESSION MODEL CANDIDATE SHOOTOUT" in output
    assert "points_robust" in output
    assert "category_robust" in output
    assert "Regression mapping" in output
    assert "PTS is always derived from projected G + A" in output

import pytest

from apollo import cli_v35
from apollo.db import Database
from apollo.draft.overall_finishing import (
    OVERALL_FINISHING_MODEL_VERSION,
    build_overall_finishing_context_ratio,
    correction_factor,
)
from apollo.draft.projections import MODEL_VERSION, ProjectionSeason, build_skater_projection
from apollo.services.draft_projections import project_skater
from apollo.services.overall_finishing import load_overall_finishing_priors
from apollo.services.regression import load_position_priors


def test_production_overall_finishing_uses_five_percent_mean_reversion():
    assert correction_factor(1.20) == pytest.approx(0.99)
    assert correction_factor(0.80) == pytest.approx(1.01)
    assert correction_factor(0.0) == pytest.approx(1.05)


def test_overall_finishing_ratio_uses_calendar_weights_and_requires_three_seasons():
    history = ((1.2, 1.0), (0.9, 1.0), (1.0, 1.0))

    assert build_overall_finishing_context_ratio(history) == pytest.approx(1.09)
    assert build_overall_finishing_context_ratio(history[:2]) is None


def test_projection_overall_finishing_changes_only_goals_and_stamps_v07():
    history = tuple(
        ProjectionSeason(
            season=season,
            games_played=80.0,
            stats={
                "goals": 40.0,
                "assists": 60.0,
                "powerPlayPoints": 20.0,
                "shots": 200.0,
                "hits": 80.0,
                "blockedShots": 40.0,
            },
        )
        for season in (20252026, 20242025, 20232024)
    )
    baseline = build_skater_projection(
        player_id=1,
        player_name="Baseline",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
    )
    corrected = build_skater_projection(
        player_id=1,
        player_name="Corrected",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
        overall_finishing_context_ratio=1.20,
    )

    assert MODEL_VERSION == "apollo-skater-baseline-v0.7"
    assert corrected.stats["goals"] == pytest.approx(baseline.stats["goals"] * 0.99)
    for stat_name in ("assists", "powerPlayPoints", "shots", "hits", "blockedShots"):
        assert corrected.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert corrected.overall_finishing_model_version == OVERALL_FINISHING_MODEL_VERSION
    assert baseline.overall_finishing_model_version is None


def _insert_player(database: Database, first_name: str, last_name: str, position: str) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES (?, ?, ?, 'EDM')
            """,
            (first_name, last_name, position),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', ?)",
            (player_id, str(9950000 + player_id)),
        )
    return player_id


def _insert_source_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    shooting_pct: float | None,
    shots: float,
    goals: float = 40.0,
    assists: float = 60.0,
) -> None:
    stats = {
        "gamesPlayed": 80.0,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": 20.0,
        "shots": shots,
        "hits": 80.0,
        "blockedShots": 40.0,
    }
    if shooting_pct is not None:
        stats["shotTypeShootingPct"] = shooting_pct
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_overall_finishing_priors_are_shot_weighted_split_by_position_and_source_only(tmp_path):
    database = Database(tmp_path / "apollo.db")
    forward_a = _insert_player(database, "Forward", "A", "C")
    forward_b = _insert_player(database, "Forward", "B", "LW")
    defense = _insert_player(database, "Defense", "A", "D")
    _insert_source_season(database, forward_a, 20252026, shooting_pct=0.20, shots=100.0)
    _insert_source_season(database, forward_b, 20252026, shooting_pct=0.10, shots=300.0)
    _insert_source_season(database, defense, 20252026, shooting_pct=0.08, shots=200.0)
    _insert_source_season(database, forward_a, 20262027, shooting_pct=0.99, shots=1000.0)

    priors = load_overall_finishing_priors(database, (20252026,))

    assert priors[(20252026, "F")] == pytest.approx(0.125)
    assert priors[(20252026, "D")] == pytest.approx(0.08)
    assert all(key[0] != 20262027 for key in priors)


def test_live_projection_applies_source_only_overall_finishing_and_missing_context_falls_back(
    tmp_path,
):
    database = Database(tmp_path / "apollo.db")
    hot = _insert_player(database, "Hot", "Finisher", "C")
    peer = _insert_player(database, "Peer", "Skater", "RW")
    fallback = _insert_player(database, "No", "Context", "D")
    source_seasons = (20252026, 20242025, 20232024)
    for season in source_seasons:
        _insert_source_season(database, hot, season, shooting_pct=0.20, shots=200.0)
        _insert_source_season(
            database,
            peer,
            season,
            shooting_pct=0.10,
            shots=200.0,
            goals=20.0,
            assists=30.0,
        )
        _insert_source_season(database, fallback, season, shooting_pct=None, shots=150.0)

    regression_priors = load_position_priors(database, source_seasons)
    hot_history = tuple(
        ProjectionSeason(
            season=season,
            games_played=80.0,
            stats={
                "goals": 40.0,
                "assists": 60.0,
                "powerPlayPoints": 20.0,
                "shots": 200.0,
                "hits": 80.0,
                "blockedShots": 40.0,
                "shotTypeShootingPct": 0.20,
            },
        )
        for season in source_seasons
    )
    hot_baseline = build_skater_projection(
        player_id=hot,
        player_name="Hot Finisher",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=hot_history,
        regression_priors=regression_priors,
    )

    hot_projection = project_skater(database, "Hot Finisher", 20262027)
    fallback_projection = project_skater(database, "No Context", 20262027)

    expected_ratio = 0.20 / 0.15
    assert hot_projection.stats["goals"] == pytest.approx(
        hot_baseline.stats["goals"] * correction_factor(expected_ratio)
    )
    assert hot_projection.overall_finishing_model_version == OVERALL_FINISHING_MODEL_VERSION
    assert fallback_projection.overall_finishing_model_version is None


def test_overall_finishing_production_cli_contract():
    args = cli_v35.build_parser().parse_args(
        ["draft", "overall-finishing-production-gate", "--season", "20252026"]
    )

    assert args.command == "draft"
    assert args.draft_command == "overall-finishing-production-gate"
    assert args.years == 3
    assert args.min_actual_games == 20
    assert args.min_history_seasons == 3

import pytest

from apollo.db import Database
from apollo.draft.projections import ProjectionSeason, build_skater_projection
from apollo.draft.shooting_context import (
    SHOOTING_CONTEXT_MODEL_VERSION,
    build_shooting_context_ratio,
    correction_factor,
)
from apollo.services.draft_projections import project_skater
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors


def test_production_shooting_context_uses_ten_percent_mean_reversion():
    assert correction_factor(1.20) == pytest.approx(0.98)
    assert correction_factor(0.80) == pytest.approx(1.02)


def test_shooting_context_ratio_uses_calendar_weights():
    ratio = build_shooting_context_ratio(
        ((0.12, 0.10), (0.09, 0.10), (0.10, 0.10))
    )

    assert ratio == pytest.approx(1.09)


def test_projection_shooting_context_changes_only_goals_and_assists():
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
        shooting_context_ratio=1.20,
    )

    assert corrected.stats["goals"] == pytest.approx(baseline.stats["goals"] * 0.98)
    assert corrected.stats["assists"] == pytest.approx(baseline.stats["assists"] * 0.98)
    for stat_name in ("powerPlayPoints", "shots", "hits", "blockedShots"):
        assert corrected.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert corrected.shooting_context_model_version == SHOOTING_CONTEXT_MODEL_VERSION
    assert baseline.shooting_context_model_version is None


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
            (player_id, str(9800000 + player_id)),
        )
    return player_id


def _insert_source_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    shooting_pct: float,
    goals: float = 40.0,
    assists: float = 60.0,
) -> None:
    stats = {
        "gamesPlayed": games,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": 20.0,
        "shots": 200.0,
        "hits": 80.0,
        "blockedShots": 40.0,
        "shootingPct5v5": shooting_pct,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            ) VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def test_shooting_context_priors_are_gp_weighted_and_split_by_position(tmp_path):
    database = Database(tmp_path / "apollo.db")
    forward_a = _insert_player(database, "Forward", "A", "C")
    forward_b = _insert_player(database, "Forward", "B", "LW")
    defense = _insert_player(database, "Defense", "A", "D")
    _insert_source_season(database, forward_a, 20252026, games=80.0, shooting_pct=0.12)
    _insert_source_season(database, forward_b, 20252026, games=40.0, shooting_pct=0.08)
    _insert_source_season(database, defense, 20252026, games=80.0, shooting_pct=0.09)

    priors = load_shooting_context_priors(database, (20252026,))

    assert priors[(20252026, "F")] == pytest.approx((0.12 * 80 + 0.08 * 40) / 120)
    assert priors[(20252026, "D")] == pytest.approx(0.09)


def test_live_projection_applies_source_only_shooting_context(tmp_path):
    database = Database(tmp_path / "apollo.db")
    hot = _insert_player(database, "Hot", "Context", "C")
    peer = _insert_player(database, "Peer", "Skater", "RW")
    source_seasons = (20252026, 20242025, 20232024)
    for season in source_seasons:
        _insert_source_season(database, hot, season, games=80.0, shooting_pct=0.12)
        _insert_source_season(
            database,
            peer,
            season,
            games=80.0,
            shooting_pct=0.08,
            goals=20.0,
            assists=30.0,
        )

    regression_priors = load_position_priors(database, source_seasons)
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
                "shootingPct5v5": 0.12,
            },
        )
        for season in source_seasons
    )
    baseline = build_skater_projection(
        player_id=hot,
        player_name="Hot Context",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
        regression_priors=regression_priors,
    )

    projection = project_skater(database, "Hot Context", 20262027)

    assert projection.stats["goals"] == pytest.approx(baseline.stats["goals"] * 0.98)
    assert projection.stats["assists"] == pytest.approx(baseline.stats["assists"] * 0.98)
    assert projection.stats["powerPlayPoints"] == pytest.approx(
        baseline.stats["powerPlayPoints"]
    )
    assert projection.shooting_context_model_version == SHOOTING_CONTEXT_MODEL_VERSION

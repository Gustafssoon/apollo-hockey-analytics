import pytest

from apollo.db import Database
from apollo.draft.assist_rate import (
    ASSIST_RATE_MODEL_VERSION,
    build_assist_rate_context_ratio,
    correction_factor,
)
from apollo.draft.projections import ProjectionSeason, build_skater_projection
from apollo.services.assist_rate import load_assist_rate_priors
from apollo.services.draft_projections import project_skater
from apollo.services.regression import load_position_priors


def test_production_assist_rate_uses_ten_percent_mean_reversion():
    assert correction_factor(1.20) == pytest.approx(0.98)
    assert correction_factor(0.80) == pytest.approx(1.02)
    assert correction_factor(0.0) == pytest.approx(1.10)


def test_assist_rate_ratio_uses_calendar_weights_and_requires_three_seasons():
    history = ((1.2, 1.0), (0.9, 1.0), (1.0, 1.0))

    assert build_assist_rate_context_ratio(history) == pytest.approx(1.09)
    assert build_assist_rate_context_ratio(history[:2]) is None


def test_projection_assist_rate_changes_only_assists():
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
        assist_rate_context_ratio=1.20,
    )

    assert corrected.stats["assists"] == pytest.approx(baseline.stats["assists"] * 0.98)
    for stat_name in ("goals", "powerPlayPoints", "shots", "hits", "blockedShots"):
        assert corrected.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert corrected.assist_rate_model_version == ASSIST_RATE_MODEL_VERSION
    assert baseline.assist_rate_model_version is None


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
            (player_id, str(9900000 + player_id)),
        )
    return player_id


def _insert_source_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    toi_per_game_5v5: float,
    assist_rate: float,
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
        "timeOnIcePerGame5v5": toi_per_game_5v5,
        "assistsPer605v5": assist_rate,
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


def test_assist_rate_priors_are_toi_weighted_and_split_by_position(tmp_path):
    database = Database(tmp_path / "apollo.db")
    forward_a = _insert_player(database, "Forward", "A", "C")
    forward_b = _insert_player(database, "Forward", "B", "LW")
    defense = _insert_player(database, "Defense", "A", "D")
    _insert_source_season(
        database, forward_a, 20252026, games=80.0, toi_per_game_5v5=900.0, assist_rate=2.0
    )
    _insert_source_season(
        database, forward_b, 20252026, games=40.0, toi_per_game_5v5=600.0, assist_rate=1.0
    )
    _insert_source_season(
        database, defense, 20252026, games=80.0, toi_per_game_5v5=1000.0, assist_rate=1.4
    )

    priors = load_assist_rate_priors(database, (20252026,))
    exposure_a = 80.0 * 900.0
    exposure_b = 40.0 * 600.0

    assert priors[(20252026, "F")] == pytest.approx(
        (2.0 * exposure_a + 1.0 * exposure_b) / (exposure_a + exposure_b)
    )
    assert priors[(20252026, "D")] == pytest.approx(1.4)


def test_live_projection_applies_source_only_assist_rate_context(tmp_path):
    database = Database(tmp_path / "apollo.db")
    hot = _insert_player(database, "Hot", "Assists", "C")
    peer = _insert_player(database, "Peer", "Skater", "RW")
    source_seasons = (20252026, 20242025, 20232024)
    for season in source_seasons:
        _insert_source_season(
            database,
            hot,
            season,
            games=80.0,
            toi_per_game_5v5=900.0,
            assist_rate=2.4,
        )
        _insert_source_season(
            database,
            peer,
            season,
            games=80.0,
            toi_per_game_5v5=900.0,
            assist_rate=1.6,
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
                "timeOnIcePerGame5v5": 900.0,
                "assistsPer605v5": 2.4,
            },
        )
        for season in source_seasons
    )
    baseline = build_skater_projection(
        player_id=hot,
        player_name="Hot Assists",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
        regression_priors=regression_priors,
    )

    projection = project_skater(database, "Hot Assists", 20262027)

    assert projection.stats["assists"] == pytest.approx(baseline.stats["assists"] * 0.98)
    for stat_name in ("goals", "powerPlayPoints", "shots", "hits", "blockedShots"):
        assert projection.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert projection.assist_rate_model_version == ASSIST_RATE_MODEL_VERSION

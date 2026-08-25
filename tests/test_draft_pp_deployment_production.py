import pytest

from apollo import cli_v39
from apollo.db import Database
from apollo.draft.pp_deployment import (
    PP_DEPLOYMENT_MODEL_VERSION,
    build_pp_deployment_context_ratio,
    correction_factor,
)
from apollo.draft.pp_deployment_production_gate import (
    PPDeploymentProductionGateResult,
    PPDeploymentProductionSeasonCheck,
)
from apollo.draft.projections import ProjectionSeason, build_skater_projection
from apollo.services.draft_projections import project_skater
from apollo.services.pp_deployment import load_pp_deployment_priors
from apollo.services.regression import load_position_priors


def test_production_pp_deployment_uses_five_percent_mean_reversion():
    assert correction_factor(1.20) == pytest.approx(0.99)
    assert correction_factor(0.80) == pytest.approx(1.01)
    assert correction_factor(0.0) == pytest.approx(1.05)


def test_pp_deployment_ratio_uses_calendar_weights_and_requires_three_seasons():
    history = ((180.0, 150.0), (135.0, 150.0), (150.0, 150.0))

    assert build_pp_deployment_context_ratio(history) == pytest.approx(1.09)
    assert build_pp_deployment_context_ratio(history[:2]) is None


def test_projection_pp_deployment_changes_only_ppp():
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
        pp_deployment_context_ratio=1.20,
    )

    assert corrected.stats["powerPlayPoints"] == pytest.approx(
        baseline.stats["powerPlayPoints"] * 0.99
    )
    for stat_name in ("goals", "assists", "shots", "hits", "blockedShots"):
        assert corrected.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert corrected.pp_deployment_model_version == PP_DEPLOYMENT_MODEL_VERSION
    assert baseline.pp_deployment_model_version is None


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
            (player_id, str(9700000 + player_id)),
        )
    return player_id


def _insert_source_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    pp_toi_per_game: float,
    goals: float = 40.0,
    assists: float = 60.0,
    ppp: float = 20.0,
) -> None:
    stats = {
        "gamesPlayed": games,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": ppp,
        "shots": 200.0,
        "hits": 80.0,
        "blockedShots": 40.0,
        "powerPlayTimeOnIcePerGame": pp_toi_per_game,
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


def test_pp_deployment_priors_are_gp_weighted_and_split_by_position(tmp_path):
    database = Database(tmp_path / "apollo.db")
    forward_a = _insert_player(database, "Forward", "A", "C")
    forward_b = _insert_player(database, "Forward", "B", "LW")
    defense = _insert_player(database, "Defense", "A", "D")
    _insert_source_season(
        database, forward_a, 20252026, games=80.0, pp_toi_per_game=180.0
    )
    _insert_source_season(
        database, forward_b, 20252026, games=40.0, pp_toi_per_game=120.0
    )
    _insert_source_season(
        database, defense, 20252026, games=80.0, pp_toi_per_game=90.0
    )

    priors = load_pp_deployment_priors(database, (20252026,))

    assert priors[(20252026, "F")] == pytest.approx(160.0)
    assert priors[(20252026, "D")] == pytest.approx(90.0)


def test_live_projection_applies_source_only_pp_deployment_context(tmp_path):
    database = Database(tmp_path / "apollo.db")
    hot = _insert_player(database, "Hot", "Powerplay", "C")
    peer = _insert_player(database, "Peer", "Skater", "RW")
    source_seasons = (20252026, 20242025, 20232024)
    for season in source_seasons:
        _insert_source_season(
            database,
            hot,
            season,
            games=80.0,
            pp_toi_per_game=180.0,
            ppp=30.0,
        )
        _insert_source_season(
            database,
            peer,
            season,
            games=80.0,
            pp_toi_per_game=120.0,
            goals=20.0,
            assists=30.0,
            ppp=10.0,
        )

    regression_priors = load_position_priors(database, source_seasons)
    history = tuple(
        ProjectionSeason(
            season=season,
            games_played=80.0,
            stats={
                "goals": 40.0,
                "assists": 60.0,
                "powerPlayPoints": 30.0,
                "shots": 200.0,
                "hits": 80.0,
                "blockedShots": 40.0,
                "powerPlayTimeOnIcePerGame": 180.0,
            },
        )
        for season in source_seasons
    )
    baseline = build_skater_projection(
        player_id=hot,
        player_name="Hot Powerplay",
        team_abbrev="EDM",
        position="C",
        target_season=20262027,
        history=history,
        regression_priors=regression_priors,
    )

    projection = project_skater(database, "Hot Powerplay", 20262027)

    assert projection.stats["powerPlayPoints"] == pytest.approx(
        baseline.stats["powerPlayPoints"] * 0.99
    )
    for stat_name in ("goals", "assists", "shots", "hits", "blockedShots"):
        assert projection.stats[stat_name] == pytest.approx(baseline.stats[stat_name])
    assert projection.pp_deployment_model_version == PP_DEPLOYMENT_MODEL_VERSION


def test_pp_deployment_production_gate_contract_and_cli():
    checks = (
        PPDeploymentProductionSeasonCheck(20252026, 10, 10, True),
        PPDeploymentProductionSeasonCheck(20242025, 9, 9, True),
    )
    result = PPDeploymentProductionGateResult(
        target_seasons=(20252026, 20242025),
        season_checks=checks,
        aggregate=None,  # type: ignore[arg-type]
    )
    assert result.exact_candidate_equivalence is True

    args = cli_v39.build_parser().parse_args(
        ["draft", "pp-deployment-production-gate", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "pp-deployment-production-gate"
    assert args.years == 3
    assert args.min_history_seasons == 3

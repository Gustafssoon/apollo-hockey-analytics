import pytest

from apollo import cli_v11
from apollo.db import Database
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.services.draft_projections import project_skater


def _insert_skater(
    database: Database,
    *,
    position: str = "C",
    birth_date: str | None = "1997-01-13",
) -> int:
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Connor', 'McDavid', ?, 'EDM')
            """,
            (position,),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', '8478402')
            """,
            (player_id,),
        )
        if birth_date is not None:
            connection.execute(
                """
                INSERT INTO nhl_player_profile (
                    player_id, is_active, team_abbrev, position, sweater_number, birth_date, fetched_at
                ) VALUES (?, 1, 'EDM', ?, 97, ?, '2026-08-24T00:00:00Z')
                """,
                (player_id, position, birth_date),
            )
    return player_id


def _insert_season(
    database: Database,
    player_id: int,
    season: int,
    *,
    games: float,
    goals: float,
    assists: float,
    ppp: float,
    shots: float,
    hits: float,
    blocks: float,
) -> None:
    stats = {
        "gamesPlayed": games,
        "goals": goals,
        "assists": assists,
        "powerPlayPoints": ppp,
        "shots": shots,
        "hits": hits,
        "blockedShots": blocks,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            )
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def _seed_three_seasons(database: Database, *, birth_date: str | None = "1997-01-13") -> None:
    player_id = _insert_skater(database, birth_date=birth_date)
    _insert_season(
        database,
        player_id,
        20252026,
        games=80,
        goals=40,
        assists=80,
        ppp=40,
        shots=240,
        hits=80,
        blocks=40,
    )
    _insert_season(
        database,
        player_id,
        20242025,
        games=70,
        goals=28,
        assists=56,
        ppp=28,
        shots=210,
        hits=70,
        blocks=35,
    )
    _insert_season(
        database,
        player_id,
        20232024,
        games=60,
        goals=18,
        assists=36,
        ppp=18,
        shots=180,
        hits=60,
        blocks=30,
    )


def test_previous_seasons_for_target_season():
    assert previous_seasons(20262027) == (20252026, 20242025, 20232024)


def test_three_season_skater_projection_uses_availability_and_age_adjusted_rates(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_three_seasons(database)

    projection = project_skater(database, "Connor McDavid", 20262027)

    assert projection.projected_games == pytest.approx(78.5)
    assert projection.stats["goals"] == pytest.approx(34.3869636553)
    assert projection.stats["assists"] == pytest.approx(68.7739273106)
    assert projection.stats["powerPlayPoints"] == pytest.approx(34.3869636553)
    assert projection.stats["shots"] == pytest.approx(228.9947383646)
    assert projection.stats["hits"] == pytest.approx(76.3315794549)
    assert projection.stats["blockedShots"] == pytest.approx(38.1657897274)
    assert projection.source_seasons == (20252026, 20242025, 20232024)
    assert projection.model_version == "apollo-skater-baseline-v0.6"
    assert projection.availability_model_version == "apollo-availability-shrink50-v0.1"
    assert projection.age_model_version == "apollo-age-medium-v0.1"
    assert projection.regression_model_version == "apollo-regression-points-robust-v0.1"
    assert projection.shooting_context_model_version is None
    assert projection.assist_rate_model_version is None


def test_missing_latest_season_keeps_calendar_weights(tmp_path):
    database = Database(tmp_path / "apollo.db")
    player_id = _insert_skater(database)
    _insert_season(
        database,
        player_id,
        20242025,
        games=70,
        goals=28,
        assists=56,
        ppp=28,
        shots=210,
        hits=70,
        blocks=35,
    )
    _insert_season(
        database,
        player_id,
        20232024,
        games=60,
        goals=18,
        assists=36,
        ppp=18,
        shots=180,
        hits=60,
        blocks=30,
    )

    projection = project_skater(database, "Connor McDavid", 20262027)

    assert projection.projected_games == pytest.approx(74.75)
    assert projection.stats["goals"] == pytest.approx(26.9466017080)
    assert projection.source_seasons == (20242025, 20232024)
    assert projection.regression_model_version == "apollo-regression-points-robust-v0.1"
    assert projection.shooting_context_model_version is None
    assert projection.assist_rate_model_version is None


def test_projection_falls_back_to_neutral_rates_without_birth_date(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_three_seasons(database, birth_date=None)

    projection = project_skater(database, "Connor McDavid", 20262027)

    assert projection.projected_games == pytest.approx(78.5)
    assert projection.stats["goals"] == pytest.approx(35.325)
    assert projection.stats["assists"] == pytest.approx(70.65)
    assert projection.age_model_version is None
    assert projection.regression_model_version == "apollo-regression-points-robust-v0.1"
    assert projection.shooting_context_model_version is None
    assert projection.assist_rate_model_version is None


def test_projection_rejects_goalies_for_v01(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _insert_skater(database, position="G")

    with pytest.raises(ProjectionError, match="Goalie projections are not implemented"):
        project_skater(database, "Connor McDavid", 20262027)


def test_projection_requires_historical_data(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _insert_skater(database)

    with pytest.raises(ProjectionError, match="No historical NHL season data"):
        project_skater(database, "Connor McDavid", 20262027)


def test_draft_project_cli(tmp_path, capsys):
    database = Database(tmp_path / "apollo.db")
    _seed_three_seasons(database)

    cli_v11.main(
        [
            "draft",
            "project",
            "Connor McDavid",
            "--season",
            "20262027",
            "--db",
            str(database.path),
        ]
    )

    output = capsys.readouterr().out
    assert "APOLLO DRAFT PROJECTION" in output
    assert "Connor McDavid | C | EDM" in output
    assert "Target season: 2026-27" in output
    assert "Projected GP   78.5" in output
    assert "G              34.4" in output
    assert "A              68.8" in output
    assert "Source seasons: 2025-26, 2024-25, 2023-24" in output
    assert "apollo-skater-baseline-v0.6" in output
    assert "apollo-availability-shrink50-v0.1" in output
    assert "apollo-age-medium-v0.1" in output
    assert "apollo-regression-points-robust-v0.1" in output
    assert "Shooting context: not applied (context unavailable)" in output
    assert "Assist rate: not applied (context unavailable)" in output


def test_existing_draft_config_command_still_routes_through_v11(tmp_path, capsys):
    config = tmp_path / "draft.yaml"
    config.write_text(
        """league:\n  name: Test League\n  teams: 2\ndraft:\n  type: snake\n  my_slot: 1\n  rounds: 1\nroster:\n  C: 1\n  LW: 0\n  RW: 0\n  D: 0\n  G: 0\n  BN: 0\nscoring:\n  skaters:\n    G: 1\n    A: 0\n    PPP: 0\n    SOG: 0\n    HIT: 0\n    BLK: 0\n  goalies:\n    W: 1\n    SV: 0\n    GA: 0\n    SO: 0\n""",
        encoding="utf-8",
    )

    cli_v11.main(["draft", "config", "show", "--config", str(config)])

    assert "APOLLO DRAFT CONFIG" in capsys.readouterr().out

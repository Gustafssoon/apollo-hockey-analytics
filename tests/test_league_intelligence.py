from apollo.analytics import (
    build_league_ranking,
    build_league_waiver_board,
    calculate_category_needs,
    load_league_context,
)
from apollo.db import Database

SEASON = 20252026


def _insert_league(
    database: Database,
    categories: tuple[str, ...] = ("G", "A"),
    *,
    external_id: str = "league-1",
    name: str = "Test League",
) -> tuple[int, int, int]:
    database.initialize()
    with database.connect() as connection:
        league_id = int(
            connection.execute(
                "INSERT INTO league (source, external_id, name) VALUES ('yahoo', ?, ?)",
                (external_id, name),
            ).lastrowid
        )
        user_team_id = int(
            connection.execute(
                """
                INSERT INTO fantasy_team (league_id, external_id, name, is_user_team)
                VALUES (?, ?, 'User Team', 1)
                """,
                (league_id, f"{external_id}-user"),
            ).lastrowid
        )
        rival_team_id = int(
            connection.execute(
                """
                INSERT INTO fantasy_team (league_id, external_id, name, is_user_team)
                VALUES (?, ?, 'Rival Team', 0)
                """,
                (league_id, f"{external_id}-rival"),
            ).lastrowid
        )
        for label in categories:
            connection.execute(
                """
                INSERT INTO league_stat_category (league_id, abbr, display_name)
                VALUES (?, ?, ?)
                """,
                (league_id, label, label),
            )
    return league_id, user_team_id, rival_team_id


def _insert_player(
    database: Database,
    nhl_id: int,
    first_name: str,
    last_name: str,
    team: str,
    *,
    goals: float,
    assists: float,
    fantasy_team_id: int | None = None,
) -> int:
    with database.connect() as connection:
        player_id = int(
            connection.execute(
                """
                INSERT INTO player (first_name, last_name, primary_position, nhl_team)
                VALUES (?, ?, 'C', ?)
                """,
                (first_name, last_name, team),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', ?)
            """,
            (player_id, str(nhl_id)),
        )
        connection.executemany(
            """
            INSERT INTO nhl_player_season_stat (
                player_id, season, game_type, stat_name, value
            )
            VALUES (?, ?, 2, ?, ?)
            """,
            (
                (player_id, SEASON, "gamesPlayed", 10.0),
                (player_id, SEASON, "goals", goals),
                (player_id, SEASON, "assists", assists),
            ),
        )
        if fantasy_team_id is not None:
            connection.execute(
                """
                INSERT INTO roster (fantasy_team_id, player_id, status)
                VALUES (?, ?, 'active')
                """,
                (fantasy_team_id, player_id),
            )
    return player_id


def _seed_need_scenario(database: Database) -> None:
    _, user_team_id, rival_team_id = _insert_league(database)
    _insert_player(
        database,
        1,
        "User",
        "Playmaker",
        "AAA",
        goals=2,
        assists=10,
        fantasy_team_id=user_team_id,
    )
    _insert_player(
        database,
        2,
        "Rival",
        "Shooter",
        "BBB",
        goals=10,
        assists=2,
        fantasy_team_id=rival_team_id,
    )
    _insert_player(
        database,
        3,
        "Goal",
        "Specialist",
        "CCC",
        goals=9,
        assists=1,
    )
    _insert_player(
        database,
        4,
        "Assist",
        "Specialist",
        "DDD",
        goals=1,
        assists=9,
    )


def test_league_profile_reports_supported_and_unsupported_categories(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _, _, rival_team_id = _insert_league(database, ("G", "A", "FW"))
    _insert_player(
        database,
        10,
        "Rival",
        "Rostered",
        "ZZZ",
        goals=1,
        assists=1,
        fantasy_team_id=rival_team_id,
    )

    league = load_league_context(database)

    assert league.name == "Test League"
    assert league.user_team_name == "User Team"
    assert league.team_count == 2
    support = {category.label: category.supported for category in league.categories}
    assert support == {"G": True, "A": True, "FW": False}


def test_category_needs_turn_weak_category_into_higher_weight(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_need_scenario(database)

    result = calculate_category_needs(database, SEASON, min_games=1)
    needs = {need.label: need for need in result.needs}

    assert needs["G"].rank == 2
    assert needs["G"].level == "HIGH"
    assert needs["G"].weight == 2.0
    assert needs["A"].rank == 1
    assert needs["A"].level == "LOW"
    assert needs["A"].weight == 1.0


def test_league_ranking_prioritizes_player_who_fills_roster_need(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_need_scenario(database)

    result = build_league_ranking(database, SEASON, min_games=1, limit=20)
    players = {player.name: player for player in result.players}

    assert result.categories == ("G", "A")
    assert result.weights == {"G": 2.0, "A": 1.0}
    assert players["Goal Specialist"].score > players["Assist Specialist"].score
    assert players["Goal Specialist"].score > players["Goal Specialist"].raw_score


def test_league_waivers_exclude_rostered_and_apply_need_weights(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_need_scenario(database)

    result = build_league_waiver_board(
        database,
        SEASON,
        min_games=1,
        schedule_weight=0,
        trend_weight=0,
        limit=20,
    )
    names = [player.name for player in result.board.players]

    assert "User Playmaker" not in names
    assert "Rival Shooter" not in names
    assert names.index("Goal Specialist") < names.index("Assist Specialist")
    assert result.weights == {"G": 2.0, "A": 1.0}


def test_league_waivers_ignore_ownership_from_other_leagues(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _seed_need_scenario(database)
    _, other_user_team, _ = _insert_league(
        database,
        external_id="league-2",
        name="Other League",
    )

    with database.connect() as connection:
        goal_player_id = int(
            connection.execute(
                """
                SELECT id
                FROM player
                WHERE first_name = 'Goal' AND last_name = 'Specialist'
                """
            ).fetchone()["id"]
        )
        connection.execute(
            """
            INSERT INTO roster (fantasy_team_id, player_id, status)
            VALUES (?, ?, 'active')
            """,
            (other_user_team, goal_player_id),
        )

    result = build_league_waiver_board(
        database,
        SEASON,
        league_external_id="league-1",
        min_games=1,
        schedule_weight=0,
        trend_weight=0,
        limit=20,
    )

    assert "Goal Specialist" in [player.name for player in result.board.players]

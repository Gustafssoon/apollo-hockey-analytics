from apollo import cli_v52
from apollo.db import Database
from apollo.draft.goalie_team_context_foundation import (
    GOALIE_TEAM_DOMINANCE_THRESHOLD,
    GoalieTeamContextSeasonAudit,
    build_goalie_team_context_aggregate,
)
from apollo.services.goalie_team_context_foundation import run_goalie_team_context_audit_season


def test_team_context_audit_contract_and_aggregate():
    assert GOALIE_TEAM_DOMINANCE_THRESHOLD == 0.80
    season = GoalieTeamContextSeasonAudit(
        target_season=20252026,
        baseline_goalies=2,
        source_player_seasons=6,
        with_game_logs=5,
        with_gp_stat=6,
        gp_log_match=5,
        team_identified=5,
        dominant_team_80=4,
        multi_team=1,
        goalies_all3_team_identified=1,
        goalies_all3_dominant_80=1,
    )
    aggregate = build_goalie_team_context_aggregate((season, season))
    assert aggregate.baseline_goalies == 4
    assert aggregate.source_player_seasons == 12
    assert aggregate.dominant_team_80 == 8


def _insert_goalie(database: Database, name: str) -> int:
    first, last = name.split(" ", 1)
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO player (first_name, last_name, primary_position, nhl_team) "
            "VALUES (?, ?, 'G', 'EDM')",
            (first, last),
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) "
            "VALUES (?, 'nhl', ?)",
            (player_id, str(9200000 + player_id)),
        )
    return player_id


def _insert_season_stats(
    database: Database,
    player_id: int,
    season: int,
    *,
    starts: float,
    games_played: float,
) -> None:
    stats = {
        "gamesStarted": starts,
        "gamesPlayed": games_played,
        "wins": starts * 0.5,
        "saves": starts * 30.0,
        "goalsAgainst": starts * 2.5,
        "shutouts": starts * 0.08,
        "savePctg": 0.915,
        "goalsAgainstAvg": 2.50,
    }
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO nhl_player_season_stat
                (player_id, season, game_type, stat_name, value)
            VALUES (?, ?, 2, ?, ?)
            """,
            [(player_id, season, name, value) for name, value in stats.items()],
        )


def _insert_game_logs(
    database: Database,
    player_id: int,
    season: int,
    teams: tuple[str, ...],
) -> None:
    with database.connect() as connection:
        for index, team in enumerate(teams, start=1):
            game_id = season * 100 + index
            connection.execute(
                """
                INSERT OR IGNORE INTO nhl_game
                    (game_id, season, game_type, game_date, away_team, home_team,
                     game_state, fetched_at)
                VALUES (?, ?, 2, '2026-01-01', 'EDM', 'CGY', 'FINAL', '2026-08-26T00:00:00Z')
                """,
                (game_id, season),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO nhl_player_game
                    (player_id, game_id, team_abbrev, opponent_abbrev, home_road)
                VALUES (?, ?, ?, 'CGY', 'H')
                """,
                (player_id, game_id, team),
            )


def _build_fixture(database: Database) -> int:
    goalie = _insert_goalie(database, "Audit Goalie")
    _insert_season_stats(database, goalie, 20252026, starts=25.0, games_played=30.0)
    distributions = {
        20242025: ("EDM",) * 10,
        20232024: ("EDM",) * 8 + ("CGY",) * 2,
        20222023: ("EDM",) * 7 + ("CGY",) * 3,
    }
    for season, teams in distributions.items():
        _insert_season_stats(database, goalie, season, starts=5.0, games_played=10.0)
        _insert_game_logs(database, goalie, season, teams)
    return goalie


def test_team_context_audit_measures_logs_gp_and_team_dominance(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _build_fixture(database)

    result = run_goalie_team_context_audit_season(database, 20252026)

    assert result.baseline_goalies == 1
    assert result.source_player_seasons == 3
    assert result.with_game_logs == 3
    assert result.with_gp_stat == 3
    assert result.gp_log_match == 3
    assert result.team_identified == 3
    assert result.dominant_team_80 == 2
    assert result.multi_team == 2
    assert result.goalies_all3_team_identified == 1
    assert result.goalies_all3_dominant_80 == 0


def test_target_game_logs_never_change_source_team_audit(tmp_path):
    database = Database(tmp_path / "apollo.db")
    goalie = _build_fixture(database)
    before = run_goalie_team_context_audit_season(database, 20252026)
    _insert_game_logs(database, goalie, 20252026, ("VAN",) * 10)
    after = run_goalie_team_context_audit_season(database, 20252026)
    assert after == before


def test_team_context_audit_excludes_goalie_without_strict_three_source_seasons(tmp_path):
    database = Database(tmp_path / "apollo.db")
    _build_fixture(database)
    incomplete = _insert_goalie(database, "Incomplete Goalie")
    _insert_season_stats(database, incomplete, 20252026, starts=30.0, games_played=35.0)
    _insert_season_stats(database, incomplete, 20242025, starts=10.0, games_played=12.0)

    result = run_goalie_team_context_audit_season(database, 20252026)
    assert result.baseline_goalies == 1


def test_goalie_team_context_foundation_cli_contract():
    args = cli_v52.build_parser().parse_args(
        ["draft", "goalie-team-context-foundation-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "goalie-team-context-foundation-summary"
    assert args.years == 3
    assert args.min_actual_starts == 20

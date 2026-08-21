from datetime import date
from urllib.parse import parse_qs, urlparse

from apollo.adapters import NHLStatsAdapter
from apollo.analytics import build_waiver_board
from apollo.db import Database
from apollo.models import NHLGame, NHLPlayerData, NHLPlayerGameData, NHLStat
from apollo.services import sync_nhl_recent_form

SEASON = 20252026


def _player(
    nhl_player_id: int,
    first_name: str,
    last_name: str,
    team: str,
    position: str = "C",
) -> NHLPlayerData:
    return NHLPlayerData(
        nhl_player_id=nhl_player_id,
        first_name=first_name,
        last_name=last_name,
        team_abbrev=team,
        position=position,
        is_active=True,
        sweater_number=None,
        birth_date=None,
        season=None,
        stats=(),
    )


def _seed_season_stats(
    database: Database,
    profile: NHLPlayerData,
    *,
    points: float = 40,
) -> int:
    player_id = database.upsert_nhl_pool_player(profile)
    database.upsert_nhl_player(
        player_id,
        NHLPlayerData(
            nhl_player_id=profile.nhl_player_id,
            first_name=profile.first_name,
            last_name=profile.last_name,
            team_abbrev=profile.team_abbrev,
            position=profile.position,
            is_active=True,
            sweater_number=None,
            birth_date=None,
            season=SEASON,
            stats=(
                NHLStat("gamesPlayed", 40),
                NHLStat("goals", 20),
                NHLStat("assists", 20),
                NHLStat("points", points),
                NHLStat("powerPlayPoints", 12),
                NHLStat("shots", 120),
                NHLStat("hits", 40),
                NHLStat("blockedShots", 20),
            ),
        ),
    )
    return player_id


def _game_row(
    nhl_player_id: int,
    game_id: int,
    game_date: str,
    team: str,
    opponent: str,
    **stats: float,
) -> NHLPlayerGameData:
    return NHLPlayerGameData(
        nhl_player_id=nhl_player_id,
        game=NHLGame(
            game_id=game_id,
            season=SEASON,
            game_type=2,
            game_date=game_date,
            start_time_utc=None,
            away_team=None,
            home_team=None,
            game_state="FINAL",
        ),
        team_abbrev=team,
        opponent_abbrev=opponent,
        home_road="R",
        stats=tuple(NHLStat(name, value) for name, value in stats.items()),
    )


def test_stats_adapter_paginates_and_merges_game_reports():
    summary_rows = [
        {
            "playerId": 1,
            "gameId": 100,
            "gameDate": "2026-04-10",
            "teamAbbrev": "EDM",
            "opponentTeamAbbrev": "COL",
            "homeRoadCode": "R",
            "goals": 1,
            "assists": 2,
            "points": 3,
            "ppPoints": 1,
            "shots": 5,
        },
        {
            "playerId": 1,
            "gameId": 101,
            "gameDate": "2026-04-12",
            "teamAbbrev": "EDM",
            "opponentTeamAbbrev": "VAN",
            "homeRoadCode": "H",
            "goals": 0,
            "assists": 1,
            "points": 1,
            "ppPoints": 0,
            "shots": 3,
        },
    ]
    realtime_rows = [
        {**summary_rows[0], "hits": 4, "blockedShots": 2},
        {**summary_rows[1], "hits": 1, "blockedShots": 3},
    ]
    goalie_rows = [
        {
            "playerId": 2,
            "gameId": 200,
            "gameDate": "2026-04-11",
            "teamAbbrev": "NYI",
            "opponentTeamAbbrev": "NJD",
            "homeRoadCode": "H",
            "wins": 1,
            "shotsAgainst": 31,
            "saves": 29,
            "goalsAgainst": 2,
            "savePct": 0.935,
        }
    ]

    def fake_fetch_json(url: str):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        start = int(query["start"][0])
        limit = int(query["limit"][0])
        if "/skater/summary" in parsed.path:
            rows = summary_rows
        elif "/skater/realtime" in parsed.path:
            rows = realtime_rows
        elif "/goalie/summary" in parsed.path:
            rows = goalie_rows
        else:
            raise AssertionError(url)
        return {"total": len(rows), "data": rows[start : start + limit]}

    adapter = NHLStatsAdapter(fetch_json=fake_fetch_json)
    skaters = adapter.fetch_skater_game_stats(SEASON, page_size=1)
    goalies = adapter.fetch_goalie_game_stats(SEASON, page_size=1)

    assert len(skaters) == 2
    first_stats = {stat.name: stat.value for stat in skaters[0].stats}
    assert first_stats["points"] == 3
    assert first_stats["powerPlayPoints"] == 1
    assert first_stats["hits"] == 4
    assert first_stats["blockedShots"] == 2
    assert skaters[0].opponent_abbrev == "COL"
    assert skaters[0].home_road == "R"

    assert len(goalies) == 1
    goalie_stats = {stat.name: stat.value for stat in goalies[0].stats}
    assert goalie_stats["saves"] == 29
    assert goalie_stats["savePctg"] == 0.935


def test_stats_adapter_handles_live_100_row_game_report_cap():
    summary_rows = [
        {
            "playerId": 1000 + index,
            "gameId": 5000 + index,
            "gameDate": f"2026-04-{(index % 20) + 1:02d}",
            "teamAbbrev": "EDM",
            "opponentTeamAbbrev": "COL",
            "points": index % 4,
            "shots": index % 7,
        }
        for index in range(230)
    ]
    realtime_rows = [
        {
            "playerId": row["playerId"],
            "gameId": row["gameId"],
            "hits": 2,
            "blockedShots": 1,
        }
        for row in summary_rows
    ]
    starts: dict[str, list[int]] = {"summary": [], "realtime": []}

    def fake_fetch_json(url: str):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        start = int(query["start"][0])
        requested_limit = int(query["limit"][0])
        assert requested_limit <= 100
        sort = query["sort"][0]
        assert "gameDate" in sort
        assert "playerId" in sort
        if "/skater/summary" in parsed.path:
            key = "summary"
            rows = summary_rows
        elif "/skater/realtime" in parsed.path:
            key = "realtime"
            rows = realtime_rows
        else:
            raise AssertionError(url)
        starts[key].append(start)
        server_limit = min(requested_limit, 100)
        return {
            "total": len(rows),
            "data": rows[start : start + server_limit],
        }

    adapter = NHLStatsAdapter(fetch_json=fake_fetch_json)
    rows = adapter.fetch_skater_game_stats(SEASON, page_size=5000)

    assert len(rows) == 230
    assert starts["summary"] == [0, 100, 200]
    assert starts["realtime"] == [0, 100, 200]
    last_stats = {stat.name: stat.value for stat in rows[-1].stats}
    assert last_stats["hits"] == 2
    assert last_stats["blockedShots"] == 1


def test_recent_form_sync_matches_persists_and_is_idempotent(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    _seed_season_stats(database, _player(1, "Alpha", "Center", "EDM"))
    _seed_season_stats(database, _player(2, "Bravo", "Wing", "COL", "L"))

    class StubAdapter:
        def fetch_skater_game_stats(self, season, game_type=2, page_size=100):
            assert season == SEASON
            assert game_type == 2
            return (
                _game_row(1, 100, "2026-04-10", "EDM", "COL", points=2, shots=4),
                _game_row(1, 101, "2026-04-12", "EDM", "VAN", points=1, shots=3),
                _game_row(2, 100, "2026-04-10", "COL", "EDM", points=1, shots=2),
                _game_row(999, 999, "2026-04-10", "OLD", "EDM", points=1),
            )

        def fetch_goalie_game_stats(self, season, game_type=2, page_size=100):
            return ()

    first = sync_nhl_recent_form(database, StubAdapter(), SEASON)
    second = sync_nhl_recent_form(database, StubAdapter(), SEASON)

    assert first.skater_rows == 4
    assert first.matched_rows == 3
    assert first.unmatched_rows == 1
    assert first.players == 2
    assert first.games == 2
    assert second.matched_rows == first.matched_rows

    alpha_games = database.get_player_game_log("Alpha Center", SEASON, limit=10)
    bravo_games = database.get_player_game_log("Bravo Wing", SEASON, limit=10)
    assert len(alpha_games) == 2
    assert len(bravo_games) == 1
    assert alpha_games[0][1]["points"] == 1
    assert alpha_games[1][1]["points"] == 2


def test_batch_recent_form_drives_waiver_trend_for_multiple_players(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    _seed_season_stats(database, _player(10, "Recent", "Riser", "EDM"))
    _seed_season_stats(database, _player(11, "Recent", "Faller", "COL"))

    class StubAdapter:
        def fetch_skater_game_stats(self, season, game_type=2, page_size=100):
            rows = []
            for index in range(7):
                game_date = f"2026-04-{index + 1:02d}"
                rows.append(
                    _game_row(
                        10,
                        300 + index,
                        game_date,
                        "EDM",
                        "COL",
                        points=2,
                    )
                )
                rows.append(
                    _game_row(
                        11,
                        400 + index,
                        game_date,
                        "COL",
                        "EDM",
                        points=0,
                    )
                )
            return tuple(rows)

        def fetch_goalie_game_stats(self, season, game_type=2, page_size=100):
            return ()

    sync_nhl_recent_form(database, StubAdapter(), SEASON)
    board = build_waiver_board(
        database,
        SEASON,
        as_of=date(2026, 8, 21),
        schedule_weight=0,
        trend_weight=0.5,
        include_rostered=True,
        limit=10,
    )

    riser = next(player for player in board.players if player.name == "Recent Riser")
    faller = next(player for player in board.players if player.name == "Recent Faller")
    assert riser.trend == "UP"
    assert riser.trend_component == 0.5
    assert faller.trend == "DOWN"
    assert faller.trend_component == -0.5
    assert riser.score > faller.score

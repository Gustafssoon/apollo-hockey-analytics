from apollo.adapters import NHLSeasonStatLine, NHLStatsAdapter
from apollo.analytics import compare_players, leaderboard, rank_players
from apollo.db import Database
from apollo.models import NHLPlayerData, NHLStat
from apollo.services import sync_nhl_category_stats

SEASON = 20252026


def _player(
    player_id: int,
    first_name: str,
    last_name: str,
    team: str,
    position: str,
) -> NHLPlayerData:
    return NHLPlayerData(
        nhl_player_id=player_id,
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


def _line(player_id: int, **stats: float) -> NHLSeasonStatLine:
    return NHLSeasonStatLine(
        nhl_player_id=player_id,
        stats=tuple(NHLStat(name=name, value=value) for name, value in stats.items()),
    )


def test_stats_adapter_merges_summary_realtime_and_goalies():
    def fake_fetch_json(url: str):
        if "/skater/summary?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "gamesPlayed": 82,
                        "goals": 48,
                        "assists": 90,
                        "points": 138,
                        "ppPoints": 54,
                        "shots": 306,
                        "penaltyMinutes": 44,
                    }
                ]
            }
        if "/skater/realtime?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "hits": 55,
                        "blockedShots": 31,
                        "takeaways": 72,
                        "giveaways": 61,
                    }
                ]
            }
        if "/goalie/summary?" in url:
            return {
                "data": [
                    {
                        "playerId": 8477465,
                        "gamesPlayed": 50,
                        "wins": 28,
                        "savePct": 0.912,
                        "goalsAgainstAverage": 2.61,
                        "shutouts": 4,
                    }
                ]
            }
        raise AssertionError(url)

    adapter = NHLStatsAdapter(fetch_json=fake_fetch_json)
    skaters = adapter.fetch_skater_stats(SEASON)
    goalies = adapter.fetch_goalie_stats(SEASON)

    skater_stats = {stat.name: stat.value for stat in skaters[0].stats}
    assert skater_stats["powerPlayPoints"] == 54
    assert skater_stats["hits"] == 55
    assert skater_stats["blockedShots"] == 31

    goalie_stats = {stat.name: stat.value for stat in goalies[0].stats}
    assert goalie_stats["savePctg"] == 0.912
    assert goalie_stats["goalsAgainstAvg"] == 2.61


def test_category_stats_sync_and_skater_rankings(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    database.upsert_nhl_pool_player(_player(1, "Alpha", "Center", "EDM", "C"))
    database.upsert_nhl_pool_player(_player(2, "Bravo", "Wing", "COL", "L"))
    database.upsert_nhl_pool_player(_player(3, "Charlie", "Defense", "NYR", "D"))

    class StubAdapter:
        def fetch_skater_stats(self, season, game_type=2):
            assert season == SEASON
            assert game_type == 2
            return (
                _line(
                    1,
                    gamesPlayed=80,
                    goals=50,
                    assists=80,
                    powerPlayPoints=45,
                    shots=320,
                    hits=100,
                    blockedShots=60,
                ),
                _line(
                    2,
                    gamesPlayed=80,
                    goals=35,
                    assists=55,
                    powerPlayPoints=25,
                    shots=240,
                    hits=80,
                    blockedShots=40,
                ),
                _line(
                    3,
                    gamesPlayed=80,
                    goals=15,
                    assists=45,
                    powerPlayPoints=20,
                    shots=160,
                    hits=60,
                    blockedShots=120,
                ),
            )

        def fetch_goalie_stats(self, season, game_type=2):
            return ()

    result = sync_nhl_category_stats(database, StubAdapter(), SEASON)
    assert result.skaters == 3
    assert result.matched == 3
    assert result.unmatched == 0
    assert result.stats_written == 21

    table = rank_players(database, SEASON, min_games=10, limit=3)
    assert [player.name for player in table.players] == [
        "Alpha Center",
        "Bravo Wing",
        "Charlie Defense",
    ]
    assert table.players[0].values["SOG"] == 4.0


def test_goalie_ranking_inverts_goals_against_average(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    database.upsert_nhl_pool_player(_player(10, "Good", "Goalie", "NYI", "G"))
    database.upsert_nhl_pool_player(_player(11, "Other", "Goalie", "NJD", "G"))

    class StubAdapter:
        def fetch_skater_stats(self, season, game_type=2):
            return ()

        def fetch_goalie_stats(self, season, game_type=2):
            return (
                _line(
                    10,
                    gamesPlayed=50,
                    wins=25,
                    savePctg=0.910,
                    goalsAgainstAvg=2.30,
                    shutouts=3,
                ),
                _line(
                    11,
                    gamesPlayed=50,
                    wins=25,
                    savePctg=0.910,
                    goalsAgainstAvg=3.10,
                    shutouts=3,
                ),
            )

    sync_nhl_category_stats(database, StubAdapter(), SEASON)
    table = rank_players(
        database,
        SEASON,
        player_type="goalie",
        categories="GAA",
        min_games=1,
    )
    assert table.players[0].name == "Good Goalie"
    assert table.players[0].score > table.players[1].score


def test_leaders_and_compare_use_stored_category_stats(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    database.upsert_nhl_pool_player(_player(20, "Fast", "Shooter", "TOR", "R"))
    database.upsert_nhl_pool_player(_player(21, "Steady", "Shooter", "BOS", "R"))

    class StubAdapter:
        def fetch_skater_stats(self, season, game_type=2):
            return (
                _line(
                    20,
                    gamesPlayed=40,
                    goals=20,
                    assists=20,
                    powerPlayPoints=12,
                    shots=160,
                    hits=30,
                    blockedShots=10,
                ),
                _line(
                    21,
                    gamesPlayed=40,
                    goals=15,
                    assists=25,
                    powerPlayPoints=10,
                    shots=120,
                    hits=50,
                    blockedShots=20,
                ),
            )

        def fetch_goalie_stats(self, season, game_type=2):
            return ()

    sync_nhl_category_stats(database, StubAdapter(), SEASON)

    category, leaders = leaderboard(database, SEASON, "SOG", limit=2)
    assert category.label == "SOG"
    assert leaders[0].name == "Fast Shooter"
    assert leaders[0].value == 160

    categories, players = compare_players(
        database,
        SEASON,
        ("Fast Shooter", "Steady Shooter"),
        categories="G,SOG,HIT",
    )
    assert [category.label for category in categories] == ["G", "SOG", "HIT"]
    assert players[0].values["SOG"] == 4.0
    assert players[1].values["HIT"] == 1.25

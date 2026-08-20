from pathlib import Path

from apollo.adapters import MockYahooAdapter, NHLAdapter
from apollo.db import Database
from apollo.models import NHLPlayerData, NHLStat
from apollo.services import sync_league, sync_nhl_players

FIXTURE = Path(__file__).parents[1] / "fixtures" / "mock_league.json"


def test_nhl_adapter_matches_and_parses_player_landing():
    def fake_fetch_json(url: str):
        if "search/player" in url:
            return [
                {
                    "playerId": 8478402,
                    "name": "Connor McDavid",
                    "teamAbbrev": "EDM",
                }
            ]
        return {
            "playerId": 8478402,
            "isActive": True,
            "currentTeamAbbrev": "EDM",
            "firstName": {"default": "Connor"},
            "lastName": {"default": "McDavid"},
            "position": "C",
            "sweaterNumber": 97,
            "birthDate": "1997-01-13",
            "featuredStats": {
                "season": 20252026,
                "regularSeason": {
                    "subSeason": {
                        "gamesPlayed": 82,
                        "goals": 48,
                        "assists": 90,
                        "points": 138,
                        "shots": 306,
                    }
                },
            },
        }

    profile = NHLAdapter(fetch_json=fake_fetch_json).find_player(
        "Connor", "McDavid", "EDM"
    )

    assert profile is not None
    assert profile.nhl_player_id == 8478402
    assert profile.team_abbrev == "EDM"
    assert profile.season == 20252026
    assert {stat.name: stat.value for stat in profile.stats}["points"] == 138


def test_nhl_sync_persists_external_ids_profiles_and_stats(tmp_path):
    database = Database(tmp_path / "apollo.db")
    sync_league(database, MockYahooAdapter(FIXTURE))

    ids = {
        "Connor McDavid": 8478402,
        "Matthew Tkachuk": 8479314,
        "Jake Sanderson": 8482105,
        "Ilya Sorokin": 8481033,
        "Nathan MacKinnon": 8477492,
        "Cale Makar": 8480069,
    }

    class StubNHLAdapter:
        def find_player(self, first_name, last_name, team_abbrev=None):
            full_name = f"{first_name} {last_name}"
            player_id = ids.get(full_name)
            if player_id is None:
                return None
            return NHLPlayerData(
                nhl_player_id=player_id,
                first_name=first_name,
                last_name=last_name,
                team_abbrev=team_abbrev,
                position=None,
                is_active=True,
                sweater_number=None,
                birth_date=None,
                season=20252026,
                stats=(
                    NHLStat("gamesPlayed", 82.0),
                    NHLStat("points", 100.0),
                ),
            )

        def fetch_player(self, nhl_player_id):
            for full_name, player_id in ids.items():
                if player_id == nhl_player_id:
                    first_name, last_name = full_name.split(" ", 1)
                    return self.find_player(first_name, last_name)
            raise AssertionError(f"Unexpected NHL player id {nhl_player_id}")

    result = sync_nhl_players(database, StubNHLAdapter())

    assert result.players == 6
    assert result.matched == 6
    assert result.unmatched == 0
    assert result.stats_written == 12

    card = database.get_player_card("Connor McDavid")
    assert card is not None
    profile, stats = card
    assert profile["nhl_external_id"] == "8478402"
    assert profile["season"] == 20252026
    assert {row["stat_name"]: row["value"] for row in stats}["points"] == 100.0

    second_result = sync_nhl_players(database, StubNHLAdapter())
    assert second_result.matched == 6
    assert second_result.stats_written == 12

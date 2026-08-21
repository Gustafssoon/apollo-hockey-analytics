import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from apollo.adapters import (
    YahooCredentials,
    YahooFantasyClient,
    YahooFantasyError,
    YahooLeagueAdapter,
    YahooOAuthClient,
    YahooTokenStore,
)
from apollo.db import Database
from apollo.services import sync_league

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"


class FakeTransport:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], tuple[int, bytes]] = {}
        self.requests: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def add(self, method: str, url: str, status: int, body: str | bytes) -> None:
        encoded = body.encode() if isinstance(body, str) else body
        self.responses[(method, url)] = (status, encoded)

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        del timeout
        self.requests.append((method, url, headers, data))
        return self.responses[(method, url)]


def _league_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xmlns="https://fantasysports.yahooapis.com/fantasy/v2/base.rng">
  <league>
    <league_key>500.l.12345</league_key>
    <league_id>12345</league_id>
    <name>Real Hockey League</name>
    <settings>
      <stat_categories>
        <stats>
          <stat><stat_id>1</stat_id><enabled>1</enabled><name>Goals</name><display_name>G</display_name></stat>
          <stat><stat_id>2</stat_id><enabled>1</enabled><name>Assists</name><display_name>A</display_name></stat>
          <stat><stat_id>3</stat_id><enabled>0</enabled><name>Points</name><display_name>P</display_name></stat>
          <stat><stat_id>4</stat_id><enabled>1</enabled><name>Hits</name><display_name>HIT</display_name></stat>
        </stats>
      </stat_categories>
    </settings>
    <teams count="2">
      <team>
        <team_key>500.l.12345.t.1</team_key>
        <name>Apollo Live</name>
        <is_owned_by_current_login>1</is_owned_by_current_login>
      </team>
      <team>
        <team_key>500.l.12345.t.2</team_key>
        <name>Delphi Live</name>
        <is_owned_by_current_login>0</is_owned_by_current_login>
      </team>
    </teams>
  </league>
</fantasy_content>
"""


def _roster_xml(team_key: str, player_key: str, first: str, last: str, team: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xmlns="https://fantasysports.yahooapis.com/fantasy/v2/base.rng">
  <team>
    <team_key>{team_key}</team_key>
    <roster>
      <players count="1">
        <player>
          <player_key>{player_key}</player_key>
          <player_id>{player_key.rsplit('.', 1)[-1]}</player_id>
          <name><full>{first} {last}</full><first>{first}</first><last>{last}</last></name>
          <editorial_team_abbr>{team}</editorial_team_abbr>
          <display_position>LW,RW</display_position>
        </player>
      </players>
    </roster>
  </team>
</fantasy_content>
"""


def test_credentials_load_from_local_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("YAHOO_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("YAHOO_CONSUMER_SECRET", raising=False)
    monkeypatch.delenv("YAHOO_REDIRECT_URI", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "YAHOO_CONSUMER_KEY=client-id\n"
        "YAHOO_CONSUMER_SECRET=client-secret\n"
        "YAHOO_REDIRECT_URI=https://localhost:8080\n",
        encoding="utf-8",
    )

    credentials = YahooCredentials.load(env_file)

    assert credentials.client_id == "client-id"
    assert credentials.client_secret == "client-secret"
    assert credentials.redirect_uri == "https://localhost:8080"


def test_oauth_exchange_stores_tokens_without_credentials(tmp_path):
    transport = FakeTransport()
    transport.add(
        "POST",
        TOKEN_URL,
        200,
        json.dumps(
            {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "token_type": "bearer",
            }
        ),
    )
    credentials = YahooCredentials("client-id", "client-secret")
    oauth = YahooOAuthClient(credentials, transport=transport)
    store = YahooTokenStore(tmp_path / "token.json")

    token = oauth.exchange_code("one-time-code")
    store.save(token)
    saved = json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))

    assert saved["access_token"] == "access-1"
    assert saved["refresh_token"] == "refresh-1"
    assert "client-secret" not in (tmp_path / "token.json").read_text(encoding="utf-8")
    request = transport.requests[0]
    assert request[0] == "POST"
    assert request[2]["Authorization"].startswith("Basic ")
    assert b"grant_type=authorization_code" in (request[3] or b"")


def test_fantasy_403_preserves_authorization_diagnostic():
    transport = FakeTransport()
    transport.add(
        "GET",
        f"{BASE_URL}/game/nhl",
        403,
        json.dumps({"description": "This application is not authorized to perform this action"}),
    )
    client = YahooFantasyClient(transport=transport)

    with pytest.raises(YahooFantasyError) as error:
        client.probe("access-token")

    assert error.value.status == 403
    assert "not authorized" in error.value.description


def test_list_hockey_leagues_parses_user_collection():
    transport = FakeTransport()
    url = f"{BASE_URL}/users;use_login=1/games;game_codes=nhl;seasons=2026/leagues"
    transport.add(
        "GET",
        url,
        200,
        """<fantasy_content xmlns="https://fantasysports.yahooapis.com/fantasy/v2/base.rng">
          <users><user><games><game><leagues>
            <league><league_key>500.l.12345</league_key><name>Apollo League</name></league>
            <league><league_key>500.l.99999</league_key><name>Other League</name></league>
          </leagues></game></games></user></users>
        </fantasy_content>""",
    )
    client = YahooFantasyClient(transport=transport)

    leagues = client.list_hockey_leagues("access-token", season=2026)

    assert [(league.league_key, league.name) for league in leagues] == [
        ("500.l.12345", "Apollo League"),
        ("500.l.99999", "Other League"),
    ]


def test_live_adapter_parses_categories_teams_and_rosters():
    transport = FakeTransport()
    transport.add(
        "GET",
        f"{BASE_URL}/league/500.l.12345;out=settings,teams",
        200,
        _league_xml(),
    )
    transport.add(
        "GET",
        f"{BASE_URL}/team/500.l.12345.t.1/roster",
        200,
        _roster_xml("500.l.12345.t.1", "500.p.1", "Connor", "McDavid", "EDM"),
    )
    transport.add(
        "GET",
        f"{BASE_URL}/team/500.l.12345.t.2/roster",
        200,
        _roster_xml("500.l.12345.t.2", "500.p.2", "Nathan", "MacKinnon", "COL"),
    )
    client = YahooFantasyClient(transport=transport)
    adapter = YahooLeagueAdapter(client, "access-token", "500.l.12345")

    snapshot = adapter.fetch_league()

    assert snapshot.external_id == "500.l.12345"
    assert snapshot.name == "Real Hockey League"
    assert [category.abbr for category in snapshot.stat_categories] == ["G", "A", "HIT"]
    assert len(snapshot.teams) == 2
    assert snapshot.teams[0].is_user_team is True
    assert snapshot.teams[1].is_user_team is False
    assert snapshot.teams[0].players[0].last_name == "McDavid"
    assert snapshot.teams[0].players[0].primary_position == "L"


def test_live_yahoo_sync_reuses_existing_nhl_player_and_replaces_fixture_identity(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    with database.connect() as connection:
        player_id = int(
            connection.execute(
                """
                INSERT INTO player (first_name, last_name, primary_position, nhl_team)
                VALUES ('Connor', 'McDavid', 'C', 'EDM')
                """
            ).lastrowid
        )
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'nhl', '8478402')",
            (player_id,),
        )
        connection.execute(
            "INSERT INTO player_external_id (player_id, provider, external_id) VALUES (?, 'yahoo', 'p-1')",
            (player_id,),
        )

    transport = FakeTransport()
    transport.add(
        "GET",
        f"{BASE_URL}/league/500.l.12345;out=settings,teams",
        200,
        _league_xml().replace(
            "<team><team_key>500.l.12345.t.2</team_key><name>Delphi Live</name>"
            "<is_owned_by_current_login>0</is_owned_by_current_login></team>",
            "",
        ).replace('count="2"', 'count="1"'),
    )
    transport.add(
        "GET",
        f"{BASE_URL}/team/500.l.12345.t.1/roster",
        200,
        _roster_xml("500.l.12345.t.1", "500.p.1", "Connor", "McDavid", "EDM"),
    )
    client = YahooFantasyClient(transport=transport)

    result = sync_league(
        database,
        YahooLeagueAdapter(client, "access-token", "500.l.12345"),
    )

    assert result.players == 1
    with database.connect() as connection:
        count = int(connection.execute("SELECT COUNT(*) AS count FROM player").fetchone()["count"])
        yahoo = connection.execute(
            "SELECT player_id, external_id FROM player_external_id WHERE provider = 'yahoo'"
        ).fetchone()
    assert count == 1
    assert int(yahoo["player_id"]) == player_id
    assert yahoo["external_id"] == "500.p.1"

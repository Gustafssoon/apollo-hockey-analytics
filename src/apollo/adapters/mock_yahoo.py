import json
from pathlib import Path

from apollo.models import LeagueSnapshot, PlayerSnapshot, StatCategorySnapshot, TeamSnapshot


class MockYahooAdapter:
    """Read deterministic Yahoo-shaped fixture data without making network requests."""

    source = "yahoo"

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path

    def fetch_league(self) -> LeagueSnapshot:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))

        teams = tuple(
            TeamSnapshot(
                external_id=str(team["id"]),
                name=team["name"],
                is_user_team=bool(team.get("is_user_team", False)),
                players=tuple(
                    PlayerSnapshot(
                        external_id=str(player["id"]),
                        first_name=player["first_name"],
                        last_name=player["last_name"],
                        primary_position=player["primary_position"],
                        nhl_team=player.get("nhl_team"),
                    )
                    for player in team.get("players", [])
                ),
            )
            for team in payload["teams"]
        )

        categories = tuple(
            StatCategorySnapshot(abbr=item["abbr"], display_name=item["display_name"])
            for item in payload.get("stat_categories", [])
        )

        return LeagueSnapshot(
            source=self.source,
            external_id=str(payload["league"]["id"]),
            name=payload["league"]["name"],
            teams=teams,
            stat_categories=categories,
        )

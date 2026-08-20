from typing import Protocol

from apollo.models import LeagueSnapshot


class LeagueAdapter(Protocol):
    def fetch_league(self) -> LeagueSnapshot:
        """Return one normalized league snapshot."""

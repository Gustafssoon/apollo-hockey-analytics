from pathlib import Path

from apollo.adapters import MockYahooAdapter
from apollo.db import Database
from apollo.models import LeagueSnapshot, StatCategorySnapshot, TeamSnapshot
from apollo.services import sync_league

FIXTURE = Path(__file__).parents[1] / "fixtures" / "mock_league.json"


def test_mock_sync_populates_current_roster_and_snapshot(tmp_path):
    database = Database(tmp_path / "apollo.db")
    result = sync_league(database, MockYahooAdapter(FIXTURE))

    assert result.teams == 2
    assert result.players == 6
    assert result.roster_entries == 6
    assert result.snapshots == 6

    roster = database.get_user_roster()
    assert len(roster) == 4
    assert {row["last_name"] for row in roster} == {
        "McDavid",
        "Tkachuk",
        "Sanderson",
        "Sorokin",
    }

    with database.connect() as connection:
        snapshot_count = connection.execute(
            "SELECT COUNT(*) AS count FROM roster_snapshot"
        ).fetchone()["count"]
    assert snapshot_count == 6


def test_second_sync_replaces_current_roster_but_keeps_history(tmp_path):
    database = Database(tmp_path / "apollo.db")
    adapter = MockYahooAdapter(FIXTURE)

    sync_league(database, adapter)
    sync_league(database, adapter)

    assert len(database.get_user_roster()) == 4
    with database.connect() as connection:
        current_count = connection.execute("SELECT COUNT(*) AS count FROM roster").fetchone()["count"]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) AS count FROM roster_snapshot"
        ).fetchone()["count"]

    assert current_count == 6
    assert snapshot_count == 12


def test_sync_replaces_removed_league_categories(tmp_path):
    database = Database(tmp_path / "apollo.db")

    class StubAdapter:
        def __init__(self, categories: tuple[str, ...]) -> None:
            self.categories = categories

        def fetch_league(self) -> LeagueSnapshot:
            return LeagueSnapshot(
                source="yahoo",
                external_id="category-sync",
                name="Category Sync League",
                teams=(
                    TeamSnapshot(
                        external_id="team-1",
                        name="User Team",
                        is_user_team=True,
                        players=(),
                    ),
                ),
                stat_categories=tuple(
                    StatCategorySnapshot(abbr=label, display_name=label)
                    for label in self.categories
                ),
            )

    sync_league(database, StubAdapter(("G", "A")))
    sync_league(database, StubAdapter(("G",)))

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT lsc.abbr
            FROM league_stat_category lsc
            JOIN league l ON l.id = lsc.league_id
            WHERE l.external_id = 'category-sync'
            ORDER BY lsc.id
            """
        ).fetchall()

    assert [str(row["abbr"]) for row in rows] == ["G"]

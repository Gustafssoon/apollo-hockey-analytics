from pathlib import Path

from apollo.adapters import MockYahooAdapter
from apollo.db import Database
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

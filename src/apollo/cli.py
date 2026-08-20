import argparse
from pathlib import Path

from apollo.adapters import MockYahooAdapter
from apollo.db import Database
from apollo.services import sync_league


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="apollo.db", help="SQLite database path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apollo", description="Apollo Hockey Analytics")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the Apollo database")
    _add_db_argument(init_parser)

    sync_parser = subparsers.add_parser("sync", help="Sync league data")
    _add_db_argument(sync_parser)
    sync_parser.add_argument("--source", choices=("mock",), default="mock")
    sync_parser.add_argument("--fixture", default="fixtures/mock_league.json")

    roster_parser = subparsers.add_parser("roster", help="Show the current user's roster")
    _add_db_argument(roster_parser)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    database = Database(args.db)

    if args.command == "init":
        database.initialize()
        print(f"Apollo database initialized: {database.path}")
        return

    if args.command == "sync":
        adapter = MockYahooAdapter(Path(args.fixture))
        result = sync_league(database, adapter)
        print("League synced successfully")
        print(f"Teams: {result.teams}")
        print(f"Players: {result.players}")
        print(f"Roster entries: {result.roster_entries}")
        print(f"Roster snapshots: {result.snapshots}")
        return

    if args.command == "roster":
        database.initialize()
        rows = database.get_user_roster()
        if not rows:
            print("No user roster found. Run 'apollo sync --source mock' first.")
            return

        print("Apollo Hockey Analytics")
        print(f"\n{rows[0]['fantasy_team']} roster\n")
        for row in rows:
            team = f" ({row['nhl_team']})" if row["nhl_team"] else ""
            print(f"{row['primary_position']:<3} {row['first_name']} {row['last_name']}{team}")

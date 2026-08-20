import argparse
from pathlib import Path

from apollo.adapters import MockYahooAdapter, NHLAdapter
from apollo.db import Database
from apollo.services import sync_league, sync_nhl_players


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

    nhl_parser = subparsers.add_parser("nhl", help="NHL data commands")
    nhl_subparsers = nhl_parser.add_subparsers(dest="nhl_command", required=True)
    nhl_sync_parser = nhl_subparsers.add_parser("sync", help="Match players and sync NHL data")
    _add_db_argument(nhl_sync_parser)
    nhl_sync_parser.add_argument("--timeout", type=float, default=20.0)

    player_parser = subparsers.add_parser("player", help="Show a player's NHL data")
    _add_db_argument(player_parser)
    player_parser.add_argument("name", help='Player name, for example "Connor McDavid"')

    return parser


def _print_player(database: Database, name: str) -> None:
    database.initialize()
    card = database.get_player_card(name)
    if card is None:
        print(f'Player not found: "{name}"')
        return

    profile, stat_rows = card
    print("Apollo Hockey Analytics")
    print(f"\n{profile['first_name']} {profile['last_name']}")
    team_and_position = " | ".join(
        value
        for value in (profile["nhl_team"], profile["primary_position"])
        if value
    )
    if team_and_position:
        print(team_and_position)

    if profile["nhl_external_id"]:
        print(f"NHL ID: {profile['nhl_external_id']}")
    else:
        print("NHL ID: not synced")
        return

    if profile["season"] is None:
        print("No regular-season stats stored.")
        return

    season = str(profile["season"])
    if len(season) == 8:
        season = f"{season[:4]}-{season[6:]}"
    print(f"\nRegular season {season}")

    stats = {row["stat_name"]: row["value"] for row in stat_rows}
    display_order = (
        ("gamesPlayed", "GP"),
        ("goals", "G"),
        ("assists", "A"),
        ("points", "P"),
        ("shots", "SOG"),
        ("powerPlayPoints", "PPP"),
        ("plusMinus", "+/-"),
        ("pim", "PIM"),
        ("wins", "W"),
        ("losses", "L"),
        ("savePctg", "SV%"),
        ("goalsAgainstAvg", "GAA"),
        ("shutouts", "SHO"),
    )
    shown = False
    for stat_name, label in display_order:
        if stat_name not in stats:
            continue
        value = stats[stat_name]
        if stat_name == "savePctg":
            formatted = f"{value:.3f}"
        elif value.is_integer():
            formatted = str(int(value))
        else:
            formatted = f"{value:.2f}"
        print(f"{label:<4} {formatted}")
        shown = True

    if not shown:
        print("No displayable stats stored.")


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
        return

    if args.command == "nhl" and args.nhl_command == "sync":
        result = sync_nhl_players(database, NHLAdapter(timeout=args.timeout))
        print("NHL data sync complete")
        print(f"Players: {result.players}")
        print(f"Matched: {result.matched}")
        print(f"Unmatched: {result.unmatched}")
        print(f"Stats written: {result.stats_written}")
        return

    if args.command == "player":
        _print_player(database, args.name)

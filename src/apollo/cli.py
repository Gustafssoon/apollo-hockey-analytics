import argparse
from datetime import date
from pathlib import Path

from apollo.adapters import MockYahooAdapter, NHLAdapter
from apollo.analytics import PlayerAnalysis, WindowSummary, analyze_player
from apollo.db import Database
from apollo.services import (
    sync_league,
    sync_nhl_game_log,
    sync_nhl_player_pool,
    sync_nhl_players,
    sync_nhl_schedule,
)


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="apollo.db", help="SQLite database path")


def _add_season_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="NHL season id, for example 20252026",
    )


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

    nhl_pool_parser = nhl_subparsers.add_parser(
        "pool",
        help="Import the NHL player pool from team rosters",
    )
    _add_db_argument(nhl_pool_parser)
    _add_season_argument(nhl_pool_parser)
    nhl_pool_parser.add_argument("--timeout", type=float, default=20.0)

    nhl_game_log_parser = nhl_subparsers.add_parser(
        "game-log",
        help="Sync one player's NHL game log",
    )
    _add_db_argument(nhl_game_log_parser)
    _add_season_argument(nhl_game_log_parser)
    nhl_game_log_parser.add_argument("name")
    nhl_game_log_parser.add_argument("--game-type", type=int, default=2)
    nhl_game_log_parser.add_argument("--timeout", type=float, default=20.0)

    nhl_schedule_parser = nhl_subparsers.add_parser(
        "schedule",
        help="Sync one NHL team's season schedule",
    )
    _add_db_argument(nhl_schedule_parser)
    _add_season_argument(nhl_schedule_parser)
    nhl_schedule_parser.add_argument("team")
    nhl_schedule_parser.add_argument("--timeout", type=float, default=20.0)

    player_parser = subparsers.add_parser("player", help="Show a player's NHL data")
    _add_db_argument(player_parser)
    player_parser.add_argument("name", help='Player name, for example "Connor McDavid"')

    players_parser = subparsers.add_parser("players", help="List stored NHL players")
    _add_db_argument(players_parser)
    players_parser.add_argument("--team")
    players_parser.add_argument("--limit", type=int, default=50)

    games_parser = subparsers.add_parser("games", help="Show a stored player game log")
    _add_db_argument(games_parser)
    _add_season_argument(games_parser)
    games_parser.add_argument("name")
    games_parser.add_argument("--limit", type=int, default=10)

    schedule_parser = subparsers.add_parser("schedule", help="Show a stored NHL team schedule")
    _add_db_argument(schedule_parser)
    _add_season_argument(schedule_parser)
    schedule_parser.add_argument("team")
    schedule_parser.add_argument("--limit", type=int, default=20)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a stored player game log",
    )
    _add_db_argument(analyze_parser)
    _add_season_argument(analyze_parser)
    analyze_parser.add_argument("name")
    analyze_parser.add_argument(
        "--schedule-season",
        type=int,
        help="Season id used for upcoming schedule density; defaults to --season",
    )
    analyze_parser.add_argument(
        "--as-of",
        help="Schedule window start date in YYYY-MM-DD format; defaults to today",
    )
    analyze_parser.add_argument(
        "--schedule-days",
        type=int,
        default=7,
        help="Number of calendar days in the upcoming schedule window",
    )

    return parser


def _season_label(season: int) -> str:
    value = str(season)
    return f"{value[:4]}-{value[6:]}" if len(value) == 8 else value


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
        value for value in (profile["nhl_team"], profile["primary_position"]) if value
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

    print(f"\nRegular season {_season_label(int(profile['season']))}")
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
        value = float(stats[stat_name])
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


def _print_players(database: Database, team: str | None, limit: int) -> None:
    database.initialize()
    rows = database.get_nhl_players(team, limit)
    if not rows:
        print("No NHL players stored. Run 'apollo nhl pool --season <SEASON>' first.")
        return
    print("Apollo NHL Player Pool\n")
    for row in rows:
        number = f"#{row['sweater_number']} " if row["sweater_number"] is not None else ""
        print(
            f"{row['nhl_team'] or '-':<3} {row['primary_position']:<3} "
            f"{number}{row['first_name']} {row['last_name']} "
            f"({row['nhl_external_id']})"
        )


def _print_schedule(database: Database, team: str, season: int, limit: int) -> None:
    database.initialize()
    team = team.upper()
    rows = database.get_team_schedule(team, season, limit)
    if not rows:
        print(
            f"No stored schedule for {team} {_season_label(season)}. "
            f"Run 'apollo nhl schedule {team} --season {season}' first."
        )
        return
    print(f"{team} schedule {_season_label(season)}\n")
    for row in rows:
        if row["away_team"] == team:
            opponent = f"@ {row['home_team']}"
        else:
            opponent = f"vs {row['away_team']}"
        state = f" [{row['game_state']}]" if row["game_state"] else ""
        print(f"{row['game_date']}  {opponent:<8}{state}")


def _print_games(database: Database, name: str, season: int, limit: int) -> None:
    database.initialize()
    rows = database.get_player_game_log(name, season, limit)
    if not rows:
        print(
            f'No stored game log for "{name}" {_season_label(season)}. '
            f"Run 'apollo nhl game-log \"{name}\" --season {season}' first."
        )
        return
    print(f"{name} game log {_season_label(season)}\n")
    for game, stats in rows:
        location = "@" if game["home_road"] == "R" else "vs"
        core = []
        for key, label in (
            ("goals", "G"),
            ("assists", "A"),
            ("points", "P"),
            ("shots", "SOG"),
            ("hits", "HIT"),
            ("blockedShots", "BLK"),
            ("saves", "SV"),
        ):
            if key in stats:
                value = stats[key]
                core.append(f"{label} {int(value) if value.is_integer() else value:.3g}")
        print(
            f"{game['game_date']}  {location} {game['opponent_abbrev'] or '-':<3}  "
            + "  ".join(core)
        )


def _format_rate(window: WindowSummary, stat_name: str) -> str:
    value = window.per_game.get(stat_name)
    if value is None:
        return "-"
    if stat_name == "savePctg":
        return f"{value:.3f}"
    return f"{value:.2f}"


def _print_analysis(analysis: PlayerAnalysis) -> None:
    print("Apollo Fantasy Analytics")
    identity = " | ".join(
        value for value in (analysis.team_abbrev, analysis.position) if value
    )
    print(f"\n{analysis.player_name}")
    if identity:
        print(identity)
    print(f"Regular season {_season_label(analysis.season)}\n")

    if analysis.position == "G":
        columns = (
            ("saves", "SV/GP"),
            ("shotsAgainst", "SA/GP"),
            ("goalsAgainst", "GA/GP"),
            ("savePctg", "SV%"),
        )
    else:
        columns = (
            ("goals", "G/GP"),
            ("assists", "A/GP"),
            ("points", "P/GP"),
            ("shots", "SOG/GP"),
            ("hits", "HIT/GP"),
            ("blockedShots", "BLK/GP"),
        )

    header = f"{'Window':<9} {'GP':>3}" + "".join(f" {label:>7}" for _, label in columns)
    print(header)
    for window in analysis.windows:
        values = "".join(
            f" {_format_rate(window, stat_name):>7}" for stat_name, _ in columns
        )
        print(f"{window.label:<9} {window.games:>3}{values}")

    metric_labels = {
        "points": "P/GP",
        "goals": "G/GP",
        "shots": "SOG/GP",
        "savePctg": "SV%",
        "saves": "SV/GP",
        "wins": "W/GP",
    }
    metric = metric_labels.get(analysis.trend_metric or "", analysis.trend_metric or "metric")
    if analysis.trend_percent is None:
        print(f"\nTrend ({metric}): {analysis.trend}")
    else:
        print(f"\nTrend ({metric}, Last 7 vs season): {analysis.trend} ({analysis.trend_percent:+.1f}%)")

    schedule_label = _season_label(analysis.schedule_season)
    date_range = f"{analysis.schedule_start} to {analysis.schedule_end}"
    if analysis.upcoming_games is None:
        print(
            f"Schedule {schedule_label}: not stored for {analysis.team_abbrev or 'player team'} "
            f"({date_range})"
        )
    else:
        print(
            f"Schedule {schedule_label}: {analysis.upcoming_games} regular-season games "
            f"in next {(analysis.schedule_end - analysis.schedule_start).days + 1} days "
            f"({date_range})"
        )


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

    if args.command == "nhl" and args.nhl_command == "pool":
        result = sync_nhl_player_pool(
            database,
            NHLAdapter(timeout=args.timeout),
            args.season,
        )
        print("NHL player pool sync complete")
        print(f"Teams: {result.teams}")
        print(f"Players: {result.players}")
        return

    if args.command == "nhl" and args.nhl_command == "game-log":
        try:
            result = sync_nhl_game_log(
                database,
                NHLAdapter(timeout=args.timeout),
                args.name,
                args.season,
                args.game_type,
            )
        except LookupError as exc:
            print(exc)
            return
        print(f"NHL game log synced: {result.player_name}")
        print(f"Games: {result.games}")
        print(f"Stats written: {result.stats_written}")
        return

    if args.command == "nhl" and args.nhl_command == "schedule":
        result = sync_nhl_schedule(
            database,
            NHLAdapter(timeout=args.timeout),
            args.team,
            args.season,
        )
        print(f"NHL schedule synced: {result.team_abbrev}")
        print(f"Games: {result.games}")
        return

    if args.command == "player":
        _print_player(database, args.name)
        return

    if args.command == "players":
        _print_players(database, args.team, args.limit)
        return

    if args.command == "games":
        _print_games(database, args.name, args.season, args.limit)
        return

    if args.command == "schedule":
        _print_schedule(database, args.team, args.season, args.limit)
        return

    if args.command == "analyze":
        try:
            as_of = date.fromisoformat(args.as_of) if args.as_of else None
        except ValueError:
            print(f'Invalid --as-of date: "{args.as_of}". Use YYYY-MM-DD.')
            return
        try:
            analysis = analyze_player(
                database,
                args.name,
                args.season,
                as_of=as_of,
                schedule_season=args.schedule_season,
                schedule_days=args.schedule_days,
            )
        except LookupError as exc:
            print(exc)
            return
        _print_analysis(analysis)

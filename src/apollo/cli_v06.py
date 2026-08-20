import argparse
from datetime import date

from apollo import cli_v05 as v05_cli
from apollo.adapters import NHLAdapter
from apollo.analytics import WaiverBoard, WaiverTarget, build_waiver_board, get_player_value
from apollo.db import Database
from apollo.services import sync_nhl_schedules


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="apollo.db", help="SQLite database path")


def _add_season_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="NHL performance season id, for example 20252026",
    )


def _add_value_options(parser: argparse.ArgumentParser) -> None:
    _add_db_argument(parser)
    _add_season_argument(parser)
    parser.add_argument(
        "--schedule-season",
        type=int,
        help="Season used for upcoming schedule; defaults to --season",
    )
    parser.add_argument("--as-of", help="Schedule window start date in YYYY-MM-DD format")
    parser.add_argument("--days", type=int, default=7, help="Upcoming schedule window length")
    parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    parser.add_argument(
        "--categories",
        help="Comma-separated fantasy categories; defaults depend on --type",
    )
    parser.add_argument("--mode", choices=("total", "per-game"), default="per-game")
    parser.add_argument("--min-games", type=int, default=10)
    parser.add_argument("--position", help="Optional position filter such as C, LW, RW, D, G, or F")
    parser.add_argument("--schedule-weight", type=float, default=1.0)
    parser.add_argument("--trend-weight", type=float, default=0.5)
    parser.add_argument(
        "--off-night-threshold",
        type=int,
        default=8,
        help="A date with this many or fewer NHL games counts as an off-night",
    )
    parser.add_argument(
        "--off-night-bonus",
        type=float,
        default=0.5,
        help="Extra schedule-opportunity value for each off-night game",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = v05_cli.build_parser()
    top_level = _subparsers(parser)

    nhl_parser = top_level.choices["nhl"]
    nhl_subparsers = _subparsers(nhl_parser)
    schedules_parser = nhl_subparsers.add_parser(
        "schedules",
        help="Sync all NHL team schedules for one season",
    )
    _add_db_argument(schedules_parser)
    schedules_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="NHL schedule season id, for example 20262027",
    )
    schedules_parser.add_argument("--timeout", type=float, default=20.0)

    waivers_parser = top_level.add_parser(
        "waivers",
        help="Rank waiver or streamer candidates using value, trend, and schedule",
    )
    _add_value_options(waivers_parser)
    waivers_parser.add_argument("--limit", type=int, default=25)
    waivers_parser.add_argument(
        "--include-rostered",
        action="store_true",
        help="Include players currently present in stored fantasy rosters",
    )

    value_parser = top_level.add_parser(
        "value",
        help="Show the Apollo value breakdown for one stored player",
    )
    value_parser.add_argument("name")
    _add_value_options(value_parser)

    return parser


def _season_label(season: int) -> str:
    value = str(season)
    return f"{value[:4]}-{value[6:]}" if len(value) == 8 else value


def _parse_as_of(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f'Invalid --as-of date: "{raw}". Use YYYY-MM-DD.') from exc


def _build_board(args: argparse.Namespace, database: Database) -> WaiverBoard:
    return build_waiver_board(
        database,
        args.season,
        schedule_season=args.schedule_season,
        as_of=_parse_as_of(args.as_of),
        days=args.days,
        player_type=args.type,
        categories=args.categories,
        mode=args.mode,
        min_games=args.min_games,
        position=args.position,
        include_rostered=args.include_rostered,
        schedule_weight=args.schedule_weight,
        trend_weight=args.trend_weight,
        off_night_threshold=args.off_night_threshold,
        off_night_bonus=args.off_night_bonus,
        limit=args.limit,
    )


def _print_schedule_status(board: WaiverBoard) -> None:
    if board.schedule_complete:
        print(
            f"Schedule: complete ({board.schedule_team_count}/{board.expected_team_count} teams) | "
            f"off-night <= {board.off_night_threshold} NHL games"
        )
    else:
        print(
            f"Schedule: incomplete ({board.schedule_team_count}/{board.expected_team_count} teams); "
            "schedule component disabled"
        )
        print(
            f"Run 'apollo nhl schedules --season {board.schedule_season}' "
            "to enable league-wide schedule scoring."
        )


def _print_waiver_board(board: WaiverBoard) -> None:
    print("Apollo Waiver & Streamer Board")
    print(
        f"\n{board.player_type.title()}s | performance {_season_label(board.season)} | "
        f"{board.mode} | min GP | {board.start_date} to {board.end_date}"
    )
    print("Categories: " + ", ".join(category.label for category in board.categories))
    scope = "all stored players" if board.include_rostered else "unrostered in stored fantasy data"
    print(f"Availability scope: {scope}")
    _print_schedule_status(board)
    print(
        "Formula: category Z + "
        f"{board.schedule_weight:.2f}*schedule Z + "
        f"{board.trend_weight:.2f}*trend signal"
    )

    if not board.players:
        print("\nNo eligible players found.")
        return

    print(
        f"\n{'RK':>2} {'PLAYER':<24} {'TM':<3} {'POS':<3} {'GP':>3} "
        f"{'VALUE':>7} {'CAT':>7} {'SCH':>7} {'GMS':>3} {'OFF':>3} {'TREND':>7}"
    )
    for player in board.players:
        games = "-" if player.schedule_games is None else str(player.schedule_games)
        off_nights = "-" if player.off_night_games is None else str(player.off_night_games)
        rostered_mark = "*" if player.rostered else ""
        print(
            f"{player.rank:>2} {player.name:<24} {player.team_abbrev or '-':<3} "
            f"{player.position:<3} {player.games:>3} {player.score:>7.2f} "
            f"{player.category_score:>7.2f} {player.schedule_component:>7.2f} "
            f"{games:>3} {off_nights:>3} {player.trend + rostered_mark:>7}"
        )
    if board.include_rostered and any(player.rostered for player in board.players):
        print("\n* player is currently rostered in stored fantasy data")


def _format_category_value(label: str, value: float, mode: str) -> str:
    if label == "SV%":
        return f"{value:.3f}"
    if label == "GAA":
        return f"{value:.2f}"
    if mode == "per-game":
        return f"{value:.2f}"
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def _print_player_value(board: WaiverBoard, player: WaiverTarget) -> None:
    print("Apollo Player Value")
    print(f"\n{player.name}")
    print(f"{player.team_abbrev or '-'} | {player.position} | {player.games} GP")
    print("Roster status: " + ("rostered" if player.rostered else "unrostered"))
    print(f"\nApollo value: {player.score:.2f}")
    print(f"Category Z: {player.category_score:+.2f}")

    if player.schedule_z is None:
        print("Schedule: unavailable/incomplete; contribution +0.00")
    else:
        print(
            f"Schedule: {player.schedule_games} games, {player.off_night_games} off-night games | "
            f"Z {player.schedule_z:+.2f} | contribution {player.schedule_component:+.2f}"
        )

    if player.trend_percent is None:
        print(f"Trend: {player.trend} | contribution {player.trend_component:+.2f}")
    else:
        print(
            f"Trend: {player.trend} ({player.trend_percent:+.1f}%) | "
            f"contribution {player.trend_component:+.2f}"
        )

    category_values = " | ".join(
        f"{category.label} "
        f"{_format_category_value(category.label, player.category_values[category.label], board.mode)}"
        for category in board.categories
    )
    print("Categories: " + category_values)
    _print_schedule_status(board)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "nhl" and args.nhl_command == "schedules":
        result = sync_nhl_schedules(
            Database(args.db),
            NHLAdapter(timeout=args.timeout),
            args.season,
        )
        print("NHL league schedule sync complete")
        print(f"Teams: {result.teams}")
        print(f"Unique games: {result.games}")
        return

    if args.command == "waivers":
        try:
            board = _build_board(args, Database(args.db))
        except ValueError as exc:
            print(exc)
            return
        _print_waiver_board(board)
        return

    if args.command == "value":
        try:
            board, player = get_player_value(
                Database(args.db),
                args.name,
                args.season,
                schedule_season=args.schedule_season,
                as_of=_parse_as_of(args.as_of),
                days=args.days,
                player_type=args.type,
                categories=args.categories,
                mode=args.mode,
                min_games=args.min_games,
                position=args.position,
                schedule_weight=args.schedule_weight,
                trend_weight=args.trend_weight,
                off_night_threshold=args.off_night_threshold,
                off_night_bonus=args.off_night_bonus,
            )
        except ValueError as exc:
            print(exc)
            return
        if player is None:
            print(
                f'No eligible stored value data for "{args.name}". '
                f"Run 'apollo nhl stats --season {args.season}' first."
            )
            return
        _print_player_value(board, player)
        return

    v05_cli.main(argv)

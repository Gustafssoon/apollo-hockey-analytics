import argparse

from apollo import cli_v06 as v06_cli
from apollo.adapters import NHLStatsAdapter
from apollo.db import Database
from apollo.services import sync_nhl_recent_form


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def build_parser() -> argparse.ArgumentParser:
    parser = v06_cli.build_parser()
    top_level = _subparsers(parser)
    nhl_parser = top_level.choices["nhl"]
    nhl_subparsers = _subparsers(nhl_parser)

    recent_parser = nhl_subparsers.add_parser(
        "recent",
        help="Sync league-wide per-game NHL stats for recent-form analytics",
    )
    recent_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    recent_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="NHL season id, for example 20252026",
    )
    recent_parser.add_argument("--game-type", type=int, default=2)
    recent_parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Stats REST game-report page size (NHL API caps pages at 100)",
    )
    recent_parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "nhl" and args.nhl_command == "recent":
        result = sync_nhl_recent_form(
            Database(args.db),
            NHLStatsAdapter(timeout=args.timeout),
            args.season,
            args.game_type,
            args.page_size,
        )
        print("NHL league-wide recent form sync complete")
        print(f"Skater game rows: {result.skater_rows}")
        print(f"Goalie game rows: {result.goalie_rows}")
        print(f"Matched rows: {result.matched_rows}")
        print(f"Unmatched rows: {result.unmatched_rows}")
        print(f"Players: {result.players}")
        print(f"Unique games: {result.games}")
        print(f"Stats written: {result.stats_written}")
        return

    v06_cli.main(argv)

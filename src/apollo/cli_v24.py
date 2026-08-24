import argparse

from apollo import cli_v23 as v23_cli
from apollo.adapters.nhl_advanced_stats import NHLAdvancedStatsAdapter
from apollo.db import Database
from apollo.services.advanced_stats import sync_nhl_advanced_stats


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def build_parser() -> argparse.ArgumentParser:
    parser = v23_cli.build_parser()
    top_level = _subparsers(parser)
    nhl_parser = top_level.choices["nhl"]
    nhl_subparsers = _subparsers(nhl_parser)

    advanced_parser = nhl_subparsers.add_parser(
        "advanced",
        help="Sync NHL 5v5 shooting, possession, and percentage stats",
    )
    advanced_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    advanced_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="NHL season id, for example 20252026",
    )
    advanced_parser.add_argument("--game-type", type=int, default=2)
    advanced_parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "nhl" and args.nhl_command == "advanced":
        result = sync_nhl_advanced_stats(
            Database(args.db),
            NHLAdvancedStatsAdapter(timeout=args.timeout),
            args.season,
            args.game_type,
        )
        print("NHL advanced skater stats sync complete")
        print(f"Skaters: {result.skaters}")
        print(f"Matched: {result.matched}")
        print(f"Unmatched: {result.unmatched}")
        print(f"Stats written: {result.stats_written}")
        return

    v23_cli.main(argv)

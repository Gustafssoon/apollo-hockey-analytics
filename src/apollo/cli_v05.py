import argparse

from apollo import cli as legacy_cli
from apollo.adapters import NHLStatsAdapter
from apollo.analytics import compare_players, leaderboard, rank_players
from apollo.db import Database
from apollo.services import sync_nhl_category_stats


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
        help="NHL season id, for example 20252026",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = legacy_cli.build_parser()
    top_level = _subparsers(parser)

    nhl_parser = top_level.choices["nhl"]
    nhl_subparsers = _subparsers(nhl_parser)
    stats_parser = nhl_subparsers.add_parser(
        "stats",
        help="Sync league-wide NHL fantasy category stats",
    )
    _add_db_argument(stats_parser)
    _add_season_argument(stats_parser)
    stats_parser.add_argument("--game-type", type=int, default=2)
    stats_parser.add_argument("--timeout", type=float, default=20.0)

    rankings_parser = top_level.add_parser(
        "rankings",
        help="Rank stored NHL players by fantasy category z-scores",
    )
    _add_db_argument(rankings_parser)
    _add_season_argument(rankings_parser)
    rankings_parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    rankings_parser.add_argument(
        "--categories",
        help="Comma-separated categories such as G,A,PPP,SOG,HIT,BLK",
    )
    rankings_parser.add_argument("--mode", choices=("total", "per-game"), default="per-game")
    rankings_parser.add_argument("--min-games", type=int, default=10)
    rankings_parser.add_argument("--limit", type=int, default=25)

    leaders_parser = top_level.add_parser(
        "leaders",
        help="Show leaders for one fantasy category",
    )
    _add_db_argument(leaders_parser)
    _add_season_argument(leaders_parser)
    leaders_parser.add_argument("--stat", required=True, help="Fantasy category, for example SOG")
    leaders_parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    leaders_parser.add_argument("--mode", choices=("total", "per-game"), default="total")
    leaders_parser.add_argument("--min-games", type=int, default=1)
    leaders_parser.add_argument("--limit", type=int, default=20)

    compare_parser = top_level.add_parser(
        "compare",
        help="Compare two stored NHL players by fantasy categories",
    )
    _add_db_argument(compare_parser)
    _add_season_argument(compare_parser)
    compare_parser.add_argument("names", nargs=2, metavar="PLAYER")
    compare_parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    compare_parser.add_argument(
        "--categories",
        help="Comma-separated categories; defaults depend on --type",
    )
    compare_parser.add_argument("--mode", choices=("total", "per-game"), default="per-game")

    return parser


def _season_label(season: int) -> str:
    value = str(season)
    return f"{value[:4]}-{value[6:]}" if len(value) == 8 else value


def _format_value(label: str, value: float, mode: str) -> str:
    if label == "SV%":
        return f"{value:.3f}"
    if label == "GAA":
        return f"{value:.2f}"
    if mode == "per-game":
        return f"{value:.2f}"
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _print_rankings(args: argparse.Namespace, database: Database) -> None:
    try:
        table = rank_players(
            database,
            args.season,
            player_type=args.type,
            categories=args.categories,
            mode=args.mode,
            min_games=args.min_games,
            limit=args.limit,
        )
    except ValueError as exc:
        print(exc)
        return

    if not table.players:
        print(
            "No eligible ranking data stored. "
            f"Run 'apollo nhl stats --season {args.season}' first."
        )
        return

    print("Apollo Fantasy Rankings")
    print(
        f"\n{table.player_type.title()}s | {_season_label(table.season)} | "
        f"{table.mode} | min {args.min_games} GP"
    )
    print("Categories: " + ", ".join(category.label for category in table.categories))

    category_header = "".join(f" {category.label:>7}" for category in table.categories)
    print(f"\n{'RK':>2} {'PLAYER':<24} {'TM':<3} {'POS':<3} {'GP':>3} {'Z':>7}{category_header}")
    for player in table.players:
        values = "".join(
            f" {_format_value(category.label, player.values[category.label], table.mode):>7}"
            for category in table.categories
        )
        print(
            f"{player.rank:>2} {player.name:<24} {player.team_abbrev or '-':<3} "
            f"{player.position:<3} {player.games:>3} {player.score:>7.2f}{values}"
        )


def _print_leaders(args: argparse.Namespace, database: Database) -> None:
    try:
        category, leaders = leaderboard(
            database,
            args.season,
            args.stat,
            player_type=args.type,
            mode=args.mode,
            min_games=args.min_games,
            limit=args.limit,
        )
    except ValueError as exc:
        print(exc)
        return

    if not leaders:
        print(
            "No eligible leader data stored. "
            f"Run 'apollo nhl stats --season {args.season}' first."
        )
        return

    print(
        f"Apollo {category.label} Leaders | {_season_label(args.season)} | "
        f"{args.mode}\n"
    )
    for leader in leaders:
        value = _format_value(category.label, leader.value, args.mode)
        print(
            f"{leader.rank:>2}. {leader.name:<24} {leader.team_abbrev or '-':<3} "
            f"{leader.position:<3} GP {leader.games:<3} {category.label} {value}"
        )


def _print_comparison(args: argparse.Namespace, database: Database) -> None:
    try:
        categories, players = compare_players(
            database,
            args.season,
            tuple(args.names),
            player_type=args.type,
            categories=args.categories,
            mode=args.mode,
        )
    except ValueError as exc:
        print(exc)
        return

    if len(players) != 2:
        found = {player.name.casefold() for player in players}
        missing = [name for name in args.names if name.casefold() not in found]
        print(
            "Missing stored season stats for: "
            + ", ".join(missing)
            + f". Run 'apollo nhl stats --season {args.season}' first."
        )
        return

    print(f"Apollo Player Comparison | {_season_label(args.season)} | {args.mode}\n")
    header = f"{'CATEGORY':<10}" + "".join(f" {player.name:>20}" for player in players)
    print(header)
    for category in categories:
        values = []
        for player in players:
            value = player.values.get(category.label)
            values.append(
                "-" if value is None else _format_value(category.label, value, args.mode)
            )
        print(f"{category.label:<10}" + "".join(f" {value:>20}" for value in values))
    print(f"{'GP':<10}" + "".join(f" {player.games:>20}" for player in players))


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "nhl" and args.nhl_command == "stats":
        database = Database(args.db)
        result = sync_nhl_category_stats(
            database,
            NHLStatsAdapter(timeout=args.timeout),
            args.season,
            args.game_type,
        )
        print("NHL category stats sync complete")
        print(f"Skaters: {result.skaters}")
        print(f"Goalies: {result.goalies}")
        print(f"Matched: {result.matched}")
        print(f"Unmatched: {result.unmatched}")
        print(f"Stats written: {result.stats_written}")
        return

    if args.command == "rankings":
        _print_rankings(args, Database(args.db))
        return

    if args.command == "leaders":
        _print_leaders(args, Database(args.db))
        return

    if args.command == "compare":
        _print_comparison(args, Database(args.db))
        return

    legacy_cli.main(argv)

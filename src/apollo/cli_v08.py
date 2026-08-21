import argparse
from datetime import date

from apollo import cli_v07 as v07_cli
from apollo.analytics import (
    build_league_ranking,
    build_league_waiver_board,
    calculate_category_needs,
    load_league_context,
)
from apollo.db import Database


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    if len(text) == 8:
        return f"{text[:4]}-{text[6:]}"
    return text


def _add_league_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    parser.add_argument(
        "--league-id",
        help="Stored fantasy league external id; optional when only one league exists",
    )


def _add_analysis_args(parser: argparse.ArgumentParser) -> None:
    _add_league_selector(parser)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--mode", choices=("per-game", "total"), default="per-game")
    parser.add_argument("--min-games", type=int, default=10)


def build_parser() -> argparse.ArgumentParser:
    parser = v07_cli.build_parser()
    top_level = _subparsers(parser)

    league_parser = top_level.add_parser(
        "league",
        help="League-specific fantasy intelligence from stored league settings and rosters",
    )
    league_subparsers = league_parser.add_subparsers(dest="league_command", required=True)

    profile_parser = league_subparsers.add_parser(
        "profile",
        help="Show the stored league, user team, and category support",
    )
    _add_league_selector(profile_parser)

    needs_parser = league_subparsers.add_parser(
        "needs",
        help="Rank the user roster by league category and calculate category need weights",
    )
    _add_analysis_args(needs_parser)

    ranking_parser = league_subparsers.add_parser(
        "rankings",
        help="Rank players using league categories weighted toward the user team's needs",
    )
    _add_analysis_args(ranking_parser)
    ranking_parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    ranking_parser.add_argument("--limit", type=int, default=20)

    waiver_parser = league_subparsers.add_parser(
        "waivers",
        help="League-aware waiver board using category needs, schedule, and recent form",
    )
    _add_analysis_args(waiver_parser)
    waiver_parser.add_argument("--schedule-season", type=int)
    waiver_parser.add_argument("--as-of", type=date.fromisoformat)
    waiver_parser.add_argument("--days", type=int, default=7)
    waiver_parser.add_argument("--type", choices=("skater", "goalie"), default="skater")
    waiver_parser.add_argument("--position")
    waiver_parser.add_argument("--schedule-weight", type=float, default=1.0)
    waiver_parser.add_argument("--trend-weight", type=float, default=0.5)
    waiver_parser.add_argument("--off-night-threshold", type=int, default=8)
    waiver_parser.add_argument("--off-night-bonus", type=float, default=0.5)
    waiver_parser.add_argument("--limit", type=int, default=20)
    return parser


def _print_profile(database: Database, league_id: str | None) -> None:
    league = load_league_context(database, league_id)
    supported = [category.label for category in league.categories if category.supported]
    unsupported = [category.label for category in league.categories if not category.supported]

    print("Apollo League Profile")
    print()
    print(f"League: {league.name}")
    print(f"Source: {league.source}")
    print(f"External ID: {league.external_id}")
    print(f"Teams: {league.team_count}")
    print(f"User team: {league.user_team_name}")
    print(f"Categories: {', '.join(category.label for category in league.categories) or '-'}")
    print(f"Supported: {', '.join(supported) or '-'}")
    print(f"Unsupported: {', '.join(unsupported) or '-'}")


def _print_needs(database: Database, args: argparse.Namespace) -> None:
    result = calculate_category_needs(
        database,
        args.season,
        league_external_id=args.league_id,
        mode=args.mode,
        min_games=args.min_games,
    )
    print("Apollo Category Needs")
    print()
    print(
        f"{result.league.user_team_name} | {result.league.name} | "
        f"{_season_label(result.season)} | {result.mode} | min {result.min_games} GP"
    )
    print()
    print(f"{'CAT':<6} {'TYPE':<7} {'SCORE':>8} {'RK':>7} {'NEED':>8} {'WT':>6}")
    for need in result.needs:
        rank_text = f"{need.rank}/{need.team_count}"
        print(
            f"{need.label:<6} {need.player_type:<7} {need.team_score:>8.2f} "
            f"{rank_text:>7} {need.level:>8} {need.weight:>6.2f}"
        )

    unsupported = [
        category.label for category in result.league.categories if not category.supported
    ]
    if unsupported:
        print()
        print(f"Unsupported league categories excluded: {', '.join(unsupported)}")


def _print_rankings(database: Database, args: argparse.Namespace) -> None:
    result = build_league_ranking(
        database,
        args.season,
        league_external_id=args.league_id,
        player_type=args.type,
        mode=args.mode,
        min_games=args.min_games,
        limit=args.limit,
    )
    label = "Skaters" if args.type == "skater" else "Goalies"
    print("Apollo League-Aware Rankings")
    print()
    print(
        f"{result.league.name} | {label} | {_season_label(result.season)} | "
        f"{result.mode} | min {args.min_games} GP"
    )
    print(f"Categories: {', '.join(result.categories)}")
    print(
        "Need weights: "
        + " | ".join(f"{label} {weight:.2f}" for label, weight in result.weights.items())
    )
    print()
    print(f"{'RK':>2} {'PLAYER':<24} {'TM':<3} {'POS':<3} {'GP':>3} {'VALUE':>8} {'RAW':>8}")
    for player in result.players:
        print(
            f"{player.rank:>2} {player.name:<24.24} {(player.team_abbrev or '-'): <3} "
            f"{player.position:<3} {player.games:>3} {player.score:>8.2f} "
            f"{player.raw_score:>8.2f}"
        )


def _print_waivers(database: Database, args: argparse.Namespace) -> None:
    result = build_league_waiver_board(
        database,
        args.season,
        league_external_id=args.league_id,
        schedule_season=args.schedule_season,
        as_of=args.as_of,
        days=args.days,
        player_type=args.type,
        mode=args.mode,
        min_games=args.min_games,
        position=args.position,
        schedule_weight=args.schedule_weight,
        trend_weight=args.trend_weight,
        off_night_threshold=args.off_night_threshold,
        off_night_bonus=args.off_night_bonus,
        limit=args.limit,
    )
    board = result.board
    label = "Skaters" if args.type == "skater" else "Goalies"
    print("Apollo League-Aware Waiver Board")
    print()
    print(
        f"{result.league.name} | {label} | performance {_season_label(args.season)} | "
        f"{board.start_date} to {board.end_date}"
    )
    print(f"Categories: {', '.join(category.label for category in board.categories)}")
    print(
        "Need weights: "
        + " | ".join(f"{label} {weight:.2f}" for label, weight in result.weights.items())
    )
    print(f"Availability: unrostered in stored league data ({board.eligible_players} eligible)")
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
        f"Formula: need-weighted category Z + {board.schedule_weight:.2f}*schedule Z "
        f"+ {board.trend_weight:.2f}*trend signal"
    )
    print()
    print(
        f"{'RK':>2} {'PLAYER':<24} {'TM':<3} {'POS':<3} {'GP':>3} {'VALUE':>7} "
        f"{'CAT':>7} {'SCH':>7} {'GMS':>3} {'OFF':>3} {'TREND':>7}"
    )
    for player in board.players:
        games = "-" if player.schedule_games is None else str(player.schedule_games)
        off = "-" if player.off_night_games is None else str(player.off_night_games)
        schedule = 0.0 if player.schedule_z is None else player.schedule_component
        print(
            f"{player.rank:>2} {player.name:<24.24} {(player.team_abbrev or '-'): <3} "
            f"{player.position:<3} {player.games:>3} {player.score:>7.2f} "
            f"{player.category_score:>7.2f} {schedule:>7.2f} {games:>3} {off:>3} "
            f"{player.trend:>7}"
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "league":
        v07_cli.main(argv)
        return

    database = Database(args.db)
    try:
        if args.league_command == "profile":
            _print_profile(database, args.league_id)
        elif args.league_command == "needs":
            _print_needs(database, args)
        elif args.league_command == "rankings":
            _print_rankings(database, args)
        elif args.league_command == "waivers":
            _print_waivers(database, args)
    except ValueError as error:
        raise SystemExit(str(error)) from error

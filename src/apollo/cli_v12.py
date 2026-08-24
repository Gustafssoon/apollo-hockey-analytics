import argparse

from apollo import cli_v11 as v11_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.draft_backtest import run_skater_backtest

STAT_LABELS = {
    "gamesPlayed": "GP",
    "points": "PTS",
    "goals": "G",
    "assists": "A",
    "powerPlayPoints": "PPP",
    "shots": "SOG",
    "hits": "HIT",
    "blockedShots": "BLK",
}


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


def build_parser() -> argparse.ArgumentParser:
    parser = v11_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    backtest_parser = draft_subparsers.add_parser(
        "backtest",
        help="Backtest the baseline skater projection model against an actual NHL season",
    )
    backtest_parser.add_argument("--season", type=int, required=True, help="Actual target season id")
    backtest_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    backtest_parser.add_argument(
        "--min-actual-games",
        type=int,
        default=20,
        help="Minimum actual games played in the target season",
    )
    backtest_parser.add_argument(
        "--min-history-seasons",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="Minimum number of prior NHL seasons required for evaluation",
    )
    return parser


def _draft_backtest(args: argparse.Namespace) -> None:
    result = run_skater_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO PROJECTION BACKTEST")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    source_seasons = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {source_seasons}")
    print(f"Model: {result.model_version}")
    print(
        f"Filters: actual GP >= {result.min_actual_games} | "
        f"history seasons >= {result.min_history_seasons}"
    )
    print()

    print("Coverage")
    print("--------")
    print(
        f"Evaluated: {result.evaluated_players}/{result.actual_eligible_players} "
        f"({result.coverage * 100:.1f}%)"
    )
    for history_seasons, player_count in result.history_counts:
        print(f"{history_seasons} history seasons: {player_count}")
    if result.skipped_incomplete_history:
        print(f"Skipped incomplete historical stat sets: {result.skipped_incomplete_history}")
    print()

    print("Mean Absolute Error")
    print("-------------------")
    for metric in result.metrics:
        print(f"{STAT_LABELS[metric.stat_name]:<5} {metric.mae:>8.2f}")
    print()

    print("Rank Quality - Spearman rho")
    print("---------------------------")
    for metric in result.metrics:
        rho = "n/a" if metric.spearman_rho is None else f"{metric.spearman_rho:.3f}"
        print(f"{STAT_LABELS[metric.stat_name]:<5} {rho:>8}")
    print()

    print("Top-K PTS Overlap")
    print("-----------------")
    for top_k in result.top_k_points:
        print(
            f"Top {top_k.requested_k:<3} "
            f"{top_k.overlap}/{top_k.compared_k} ({top_k.overlap_rate * 100:.1f}%)"
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "backtest":
        v11_cli.main(argv)
        return

    try:
        _draft_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Backtest error: {error}") from error

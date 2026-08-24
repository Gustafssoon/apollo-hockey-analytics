import argparse

from apollo import cli_v15 as v15_cli
from apollo.db import Database
from apollo.draft.age_stat_backtest import AGE_STAT_NAMES
from apollo.draft.projections import ProjectionError
from apollo.services.age_stat_backtest import run_age_stat_backtest

STAT_LABELS = {
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


def _format_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v15_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    age_stats_parser = draft_subparsers.add_parser(
        "age-stats-backtest",
        help="Compare age adjustments separately for each skater projection stat",
    )
    age_stats_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Actual target season id",
    )
    age_stats_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    age_stats_parser.add_argument(
        "--min-actual-games",
        type=int,
        default=20,
        help="Minimum actual games played in the target season",
    )
    return parser


def _draft_age_stats_backtest(args: argparse.Namespace) -> None:
    result = run_age_stat_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
    )

    print("APOLLO AGE STAT SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(
        f"Birth dates: {result.evaluated_players}/{result.base_eligible_players} "
        f"({result.birth_date_coverage * 100:.1f}%)"
    )
    print("Age reference date: October 1 of each NHL season")
    print()
    print(
        f"{'STAT':<5} {'NEUT MAE':>9} {'BEST':<14} {'BEST MAE':>9} {'GAIN':>8} "
        f"{'NEUT RHO':>9} {'BEST RHO':>9}"
    )
    for stat_name in AGE_STAT_NAMES:
        candidates = [metric for metric in result.metrics if metric.stat_name == stat_name]
        neutral = next(metric for metric in candidates if metric.strategy_name == "neutral")
        best = min(candidates, key=lambda metric: metric.mae)
        gain = neutral.mae - best.mae
        print(
            f"{STAT_LABELS[stat_name]:<5} {neutral.mae:>9.2f} {best.strategy_name:<14} "
            f"{best.mae:>9.2f} {gain:>+8.2f} {_format_rho(neutral.spearman_rho):>9} "
            f"{_format_rho(best.spearman_rho):>9}"
        )
    print()
    print("GAIN = neutral MAE - best MAE; no stat strategy is promoted by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "age-stats-backtest":
        v15_cli.main(argv)
        return

    try:
        _draft_age_stats_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Age stat backtest error: {error}") from error

import argparse

from apollo import cli_v16 as v16_cli
from apollo.db import Database
from apollo.draft.age_stat_backtest import AGE_STAT_NAMES
from apollo.draft.projections import ProjectionError
from apollo.services.age_stat_aggregate import run_age_stat_aggregate


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


def _format_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v16_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    aggregate_parser = draft_subparsers.add_parser(
        "age-stats-summary",
        help="Aggregate age-stat strategy performance across multiple backtest seasons",
    )
    aggregate_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Most recent actual target season id",
    )
    aggregate_parser.add_argument(
        "--years",
        type=int,
        default=3,
        help="Number of consecutive target seasons to aggregate",
    )
    aggregate_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    aggregate_parser.add_argument(
        "--min-actual-games",
        type=int,
        default=20,
        help="Minimum actual games played in each target season",
    )
    return parser


def _draft_age_stats_summary(args: argparse.Namespace) -> None:
    result = run_age_stat_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
    )

    print("APOLLO AGE STAT AGGREGATE")
    print()
    seasons = ", ".join(_season_label(season) for season in result.target_seasons)
    print(f"Target seasons: {seasons}")
    print(f"Player-seasons: {result.total_player_seasons}")
    print("MAE is weighted by evaluated skaters in each target season.")
    print()
    print(
        f"{'STAT':<5} {'BEST':<14} {'W MAE':>8} {'GAIN':>8} {'YEARS+':>7} "
        f"{'RHO D':>8} {'WORST D':>8} {'RUNNER':<14} {'GAP':>7}"
    )

    for stat_name in AGE_STAT_NAMES:
        candidates = sorted(
            (strategy for strategy in result.strategies if strategy.stat_name == stat_name),
            key=lambda strategy: strategy.weighted_mae,
        )
        best = candidates[0]
        runner = candidates[1] if len(candidates) > 1 else candidates[0]
        gap = runner.weighted_mae - best.weighted_mae
        print(
            f"{v16_cli.STAT_LABELS[stat_name]:<5} {best.strategy_name:<14} "
            f"{best.weighted_mae:>8.3f} {best.mae_gain:>+8.3f} "
            f"{best.improved_years:>2}/{best.total_years:<2} "
            f"{_format_delta(best.rho_delta):>8} "
            f"{_format_delta(best.worst_rho_delta):>8} "
            f"{runner.strategy_name:<14} {gap:>7.3f}"
        )

    print()
    print("GAIN = neutral weighted MAE - best weighted MAE.")
    print("No age strategy is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "age-stats-summary":
        v16_cli.main(argv)
        return

    try:
        _draft_age_stats_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Age stat aggregate error: {error}") from error

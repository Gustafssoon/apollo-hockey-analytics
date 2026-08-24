import argparse

from apollo import cli_v21 as v21_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.draft.regression_backtest import REGRESSION_STATS
from apollo.services.regression_stat_aggregate import run_regression_stat_aggregate

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


def _format_optional(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v21_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "regression-summary",
        help="Aggregate fixed regression strategies across multiple backtest seasons",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_regression_summary(args: argparse.Namespace) -> None:
    result = run_regression_stat_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO REGRESSION STAT AGGREGATE")
    print()
    seasons = ", ".join(_season_label(season) for season in result.target_seasons)
    print(f"Target seasons: {seasons}")
    print(f"Player-seasons: {result.total_player_seasons}")
    print("MAE is weighted by evaluated skaters in each target season.")
    print("PTS is diagnostic; production PTS remains derived from G + A.")
    print()
    print(
        f"{'STAT':<5} {'BEST':<18} {'W MAE':>8} {'GAIN':>8} {'YEARS+':>7} "
        f"{'RHO D':>8} {'WORST D':>8} {'RUNNER':<18} {'GAP':>7}"
    )

    for stat_name in REGRESSION_STATS:
        candidates = [strategy for strategy in result.strategies if strategy.stat_name == stat_name]
        ordered = sorted(candidates, key=lambda strategy: strategy.weighted_mae)
        best = ordered[0]
        runner = ordered[1]
        print(
            f"{STAT_LABELS[stat_name]:<5} {best.strategy_name:<18} {best.weighted_mae:>8.3f} "
            f"{best.mae_gain:>+8.3f} {best.improved_years:>2}/{best.total_years:<4} "
            f"{_format_optional(best.rho_delta):>8} "
            f"{_format_optional(best.worst_rho_delta):>8} "
            f"{runner.strategy_name:<18} {runner.weighted_mae - best.weighted_mae:>7.3f}"
        )

    print()
    print("Fixed strategies only; no per-season adaptive winner is used.")
    print("No regression mapping is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "regression-summary":
        v21_cli.main(argv)
        return

    try:
        _draft_regression_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Regression summary error: {error}") from error

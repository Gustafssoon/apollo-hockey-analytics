import argparse

from apollo import cli_v20 as v20_cli
from apollo.cli_v18 import STAT_LABELS
from apollo.db import Database
from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError
from apollo.services.regression_backtest import run_regression_backtest


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


def _metric(strategy, stat_name: str):
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def _format_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v20_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    regression_parser = draft_subparsers.add_parser(
        "regression-backtest",
        help="Compare position-prior regression strategies against skater projection v0.3",
    )
    regression_parser.add_argument("--season", type=int, required=True)
    regression_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    regression_parser.add_argument("--min-actual-games", type=int, default=20)
    regression_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_regression_backtest(args: argparse.Namespace) -> None:
    result = run_regression_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v03"
    )
    baseline_pts = _metric(baseline, "points")

    print("APOLLO REGRESSION-TO-MEAN SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(f"Evaluated skaters: {result.evaluated_players}")
    print("Priors: source-season GP-weighted F/D rates only; target season is never used.")
    print("Control: apollo-skater-baseline-v0.3")
    print()
    print(
        f"{'STRATEGY':<18} {'PSEUDO GP':>9} {'PTS MAE':>8} {'GAIN':>8} "
        f"{'PTS RHO':>8} {'TOP25':>7} {'RAW+':>6}"
    )

    ordered = sorted(result.strategies, key=lambda strategy: _metric(strategy, "points").mae)
    for strategy in ordered:
        pts = _metric(strategy, "points")
        pseudo_games = "n/a" if strategy.pseudo_games is None else f"{strategy.pseudo_games:.0f}"
        top25 = next(item for item in strategy.top_k_points if item.requested_k == 25)
        raw_improved = sum(
            1
            for stat in SKATER_PROJECTION_STATS
            if _metric(strategy, stat).mae < _metric(baseline, stat).mae
        )
        print(
            f"{strategy.strategy_name:<18} {pseudo_games:>9} {pts.mae:>8.2f} "
            f"{baseline_pts.mae - pts.mae:>+8.2f} {_format_rho(pts.spearman_rho):>8} "
            f"{top25.overlap_rate * 100:>6.1f}% {raw_improved:>2}/6"
        )

    print()
    print("Raw-stat winners")
    print("----------------")
    print(f"{'STAT':<5} {'BEST':<18} {'MAE':>8} {'GAIN':>8}")
    for stat_name in SKATER_PROJECTION_STATS:
        best = min(result.strategies, key=lambda strategy: _metric(strategy, stat_name).mae)
        best_metric = _metric(best, stat_name)
        baseline_metric = _metric(baseline, stat_name)
        print(
            f"{STAT_LABELS[stat_name]:<5} {best.strategy_name:<18} {best_metric.mae:>8.2f} "
            f"{baseline_metric.mae - best_metric.mae:>+8.2f}"
        )

    print()
    print("No regression strategy is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "regression-backtest":
        v20_cli.main(argv)
        return

    try:
        _draft_regression_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Regression backtest error: {error}") from error

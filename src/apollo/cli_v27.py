import argparse

from apollo import cli_v26 as v26_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.advanced_shooting_regression import run_shooting_regression_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(strategy, stat_name: str):
    return next(metric for metric in strategy.metrics if metric.stat_name == stat_name)


def build_parser() -> argparse.ArgumentParser:
    parser = v26_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "shooting-regression-summary",
        help="Test source-only 5v5 on-ice shooting mean-reversion corrections against v0.4",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_shooting_regression_summary(args: argparse.Namespace) -> None:
    result = run_shooting_regression_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v04"
    )
    baseline_pts = _metric(baseline, "points")
    baseline_goals = _metric(baseline, "goals")
    baseline_assists = _metric(baseline, "assists")

    print("APOLLO 5v5 SHOOTING CONTEXT REGRESSION SHOOTOUT")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(
        f"SH%-covered player-seasons: {result.player_seasons}/"
        f"{result.baseline_player_seasons} baseline v0.4"
    )
    print("Source-season F/D SH% priors only; target stats are used only for scoring.")
    print("Correction normalizes historical on-ice SH% context toward source-season F/D mean.")
    print()
    print(
        f"{'MODEL':<16} {'PTS MAE':>8} {'GAIN':>8} {'YRS+':>5} {'WORST':>8} "
        f"{'PTS RHO':>8} {'TOP25':>7} {'G MAE':>7} {'G+':>7} {'A MAE':>7} {'A+':>7}"
    )

    ordered = sorted(
        result.strategies,
        key=lambda strategy: (
            _metric(strategy, "points").weighted_mae,
            -strategy.worst_points_mae_gain,
        ),
    )
    for strategy in ordered:
        pts = _metric(strategy, "points")
        goals = _metric(strategy, "goals")
        assists = _metric(strategy, "assists")
        rho = "n/a" if pts.weighted_rho is None else f"{pts.weighted_rho:.3f}"
        print(
            f"{strategy.strategy_name:<16} {pts.weighted_mae:>8.3f} "
            f"{baseline_pts.weighted_mae - pts.weighted_mae:>+8.3f} "
            f"{strategy.points_improved_years:>2}/{len(result.target_seasons):<2} "
            f"{strategy.worst_points_mae_gain:>+8.3f} {rho:>8} "
            f"{strategy.top25_overlap_rate * 100:>6.1f}% "
            f"{goals.weighted_mae:>7.3f} "
            f"{baseline_goals.weighted_mae - goals.weighted_mae:>+7.3f} "
            f"{assists.weighted_mae:>7.3f} "
            f"{baseline_assists.weighted_mae - assists.weighted_mae:>+7.3f}"
        )

    print()
    print("Positive GAIN/G+/A+/WORST means lower MAE than v0.4 on the same SH%-covered sample.")
    print("No shooting-context correction is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "shooting-regression-summary":
        v26_cli.main(argv)
        return

    try:
        _draft_shooting_regression_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Shooting regression summary error: {error}") from error

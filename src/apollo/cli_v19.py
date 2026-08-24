import argparse

from apollo import cli_v18 as v18_cli
from apollo.db import Database
from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError
from apollo.services.deployment_backtest import run_deployment_backtest


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
    parser = v18_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    deployment_parser = draft_subparsers.add_parser(
        "deployment-backtest",
        help="Compare TOI/deployment strategies against skater projection v0.3",
    )
    deployment_parser.add_argument("--season", type=int, required=True)
    deployment_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    deployment_parser.add_argument("--min-actual-games", type=int, default=20)
    deployment_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_deployment_backtest(args: argparse.Namespace) -> None:
    result = run_deployment_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v03"
    )
    baseline_pts = _metric(baseline, "points")

    print("APOLLO DEPLOYMENT / TOI SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(
        f"TOI coverage: {result.evaluated_players}/{result.base_eligible_players} "
        f"({result.toi_coverage * 100:.1f}%)"
    )
    print("Control: apollo-skater-baseline-v0.3")
    print()
    print(
        f"{'STRATEGY':<18} {'TOI MAE':>8} {'TOI RHO':>8} {'PTS MAE':>8} "
        f"{'GAIN':>8} {'PTS RHO':>8} {'TOP25':>7} {'RAW+':>6}"
    )

    ordered = sorted(result.strategies, key=lambda strategy: _metric(strategy, "points").mae)
    for strategy in ordered:
        pts = _metric(strategy, "points")
        raw_improved = sum(
            1
            for stat in SKATER_PROJECTION_STATS
            if _metric(strategy, stat).mae < _metric(baseline, stat).mae
        )
        toi_mae = "n/a" if strategy.projected_toi_mae is None else f"{strategy.projected_toi_mae:.2f}"
        top25 = next(item for item in strategy.top_k_points if item.requested_k == 25)
        print(
            f"{strategy.strategy_name:<18} {toi_mae:>8} "
            f"{_format_rho(strategy.projected_toi_rho):>8} {pts.mae:>8.2f} "
            f"{baseline_pts.mae - pts.mae:>+8.2f} {_format_rho(pts.spearman_rho):>8} "
            f"{top25.overlap_rate * 100:>6.1f}% {raw_improved:>2}/6"
        )

    print()
    print("Raw-stat winners")
    print("----------------")
    print(f"{'STAT':<5} {'BEST':<18} {'MAE':>8} {'GAIN':>8}")
    for stat in SKATER_PROJECTION_STATS:
        non_oracle = [
            strategy
            for strategy in result.strategies
            if strategy.strategy_name != "actual_toi_oracle"
        ]
        best = min(non_oracle, key=lambda strategy: _metric(strategy, stat).mae)
        best_metric = _metric(best, stat)
        baseline_metric = _metric(baseline, stat)
        print(
            f"{v18_cli.STAT_LABELS[stat]:<5} {best.strategy_name:<18} {best_metric.mae:>8.2f} "
            f"{baseline_metric.mae - best_metric.mae:>+8.2f}"
        )

    oracle = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "actual_toi_oracle"
    )
    oracle_pts = _metric(oracle, "points")
    print()
    print(
        "Actual-TOI oracle: "
        f"PTS MAE {oracle_pts.mae:.2f} | gain {baseline_pts.mae - oracle_pts.mae:+.2f} | "
        f"rho {_format_rho(oracle_pts.spearman_rho)}"
    )
    print("Actual target TOI is diagnostic only and is never used for real projections.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "deployment-backtest":
        v18_cli.main(argv)
        return

    try:
        _draft_deployment_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Deployment backtest error: {error}") from error

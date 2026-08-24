import argparse

from apollo import cli_v29 as v29_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.scoring_rate_regression import run_scoring_rate_regression_aggregate


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


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v29_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "scoring-rate-regression-summary",
        help="Test source-only G/60 and assist-rate mean reversion against production v0.5",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_scoring_rate_regression_summary(args: argparse.Namespace) -> None:
    result = run_scoring_rate_regression_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v05"
    )
    baseline_pts = _metric(baseline, "points")
    baseline_g = _metric(baseline, "goals")
    baseline_a = _metric(baseline, "assists")

    print("APOLLO INDIVIDUAL 5v5 SCORING-RATE REGRESSION SHOOTOUT")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(
        f"Scoring-rate-covered player-seasons: {result.player_seasons}/"
        f"{result.baseline_player_seasons} production v0.5"
    )
    print("Source-season F/D rate priors are weighted by 5v5 TOI exposure.")
    print("Corrections normalize historical individual rate context toward the F/D prior.")
    print()
    print(
        f"{'MODEL':<22} {'PTS MAE':>8} {'GAIN':>8} {'YRS+':>5} {'WORST':>8} "
        f"{'PTS RHO':>8} {'TOP25':>7} {'G MAE':>7} {'G+':>7} {'A MAE':>7} {'A+':>7}"
    )
    ordered = sorted(
        result.strategies,
        key=lambda strategy: (_metric(strategy, "points").mae, strategy.strategy_name),
    )
    for strategy in ordered:
        pts = _metric(strategy, "points")
        goals = _metric(strategy, "goals")
        assists = _metric(strategy, "assists")
        print(
            f"{strategy.strategy_name:<22} {pts.mae:>8.3f} "
            f"{baseline_pts.mae - pts.mae:>+8.3f} "
            f"{strategy.points_improved_years:>2}/{len(result.target_seasons):<2} "
            f"{strategy.worst_points_mae_gain:>+8.3f} "
            f"{_fmt_rho(pts.weighted_rho):>8} "
            f"{strategy.top25_overlap_rate * 100:>6.1f}% "
            f"{goals.mae:>7.3f} {baseline_g.mae - goals.mae:>+7.3f} "
            f"{assists.mae:>7.3f} {baseline_a.mae - assists.mae:>+7.3f}"
        )

    print()
    print("Positive GAIN/G+/A+/WORST means lower MAE than production v0.5.")
    print("Shootout only. No scoring-rate correction is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "scoring-rate-regression-summary":
        v29_cli.main(argv)
        return

    try:
        _draft_scoring_rate_regression_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Scoring-rate regression summary error: {error}") from error

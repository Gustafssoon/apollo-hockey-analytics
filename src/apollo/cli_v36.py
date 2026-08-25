import argparse

from apollo import cli_v35 as v35_cli
from apollo.db import Database
from apollo.draft.deployment_signal_backtest import (
    DEPLOYMENT_SIGNAL_NAMES,
    DEPLOYMENT_TARGET_STATS,
)
from apollo.draft.projections import MODEL_VERSION, ProjectionError
from apollo.services.deployment_signal_backtest import run_deployment_signal_aggregate

SIGNAL_LABELS = {
    "total_toi_ratio": "Total TOI/GP",
    "toi5v5_ratio": "5v5 TOI/GP",
    "pp_toi_ratio": "PP TOI/GP",
    "pp_toi_share_ratio": "PP TOI share",
}
TARGET_LABELS = {
    "powerPlayPoints": "PPP",
    "points": "PTS",
    "shots": "SOG",
    "goals": "G",
    "assists": "A",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(result, signal_name: str, target_stat: str):
    return next(
        metric
        for metric in result.metrics
        if metric.signal_name == signal_name and metric.target_stat == target_stat
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v35_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "deployment-signal-summary",
        help="Screen source-only deployment ratios against production v0.7 residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_deployment_signal_summary(args: argparse.Namespace) -> None:
    result = run_deployment_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO DEPLOYMENT / USAGE SIGNAL SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production baseline: {MODEL_VERSION}")
    print(f"Production player-seasons: {result.baseline_player_seasons}")
    print("Signals are 60/30/10 source-only usage ratios versus source-season F/D priors.")
    print("Target deployment is never used as a feature; target stats only measure residuals.")
    print("Missing 3/3 deployment context reduces that signal's N, never baseline coverage.")
    print("RHO = residual rank correlation. QD = top - bottom signal quartile residual.")
    print()

    for signal_name in DEPLOYMENT_SIGNAL_NAMES:
        coverage_metric = _metric(result, signal_name, DEPLOYMENT_TARGET_STATS[0])
        coverage = (
            coverage_metric.player_seasons / result.baseline_player_seasons
            if result.baseline_player_seasons
            else 0.0
        )
        print(
            f"{SIGNAL_LABELS[signal_name]}  "
            f"N={coverage_metric.player_seasons}  COV={coverage * 100:.1f}%"
        )
        print(f"{'TARGET':<6} {'RHO':>7} {'YRS':>5} {'QD':>8}")
        for target_stat in DEPLOYMENT_TARGET_STATS:
            metric = _metric(result, signal_name, target_stat)
            print(
                f"{TARGET_LABELS[target_stat]:<6} "
                f"{_fmt(metric.weighted_residual_rho):>7} "
                f"{metric.year_signs:>5} "
                f"{_fmt_delta(metric.weighted_quartile_delta):>8}"
            )
        print()

    print("Year-sign order matches target seasons; 0 means |rho| < 0.02.")
    print("Diagnostic only. No deployment signal is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "deployment-signal-summary":
        v35_cli.main(argv)
        return

    try:
        _draft_deployment_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Deployment signal summary error: {error}") from error

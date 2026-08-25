import argparse

from apollo import cli_v44 as v44_cli
from apollo.db import Database
from apollo.draft.goalie_workload_signal_backtest import GOALIE_WORKLOAD_SIGNALS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_workload_signal_backtest import run_goalie_workload_signal_aggregate

SIGNAL_LABELS = {
    "latest_start_share": "Latest GS share",
    "start_share_trend": "GS share trend",
    "goalie_age": "Goalie age",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v44_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-workload-signal-summary",
        help="Screen source-only goalie role signals against baseline v0.1 GS residuals",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_workload_signal_summary(args: argparse.Namespace) -> None:
    result = run_goalie_workload_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    print("APOLLO GOALIE WORKLOAD RESIDUAL SIGNAL SCREEN")
    print()
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 historical GS.")
    print(f"Baseline goalie player-seasons: {result.baseline_player_seasons}")
    print("Residual = actual target GS - baseline projected GS.")
    print("Signals use source workload or static age only; target workload is evaluation only.")
    print("RHO = signal rank correlation with GS residual. QD = top - bottom quartile residual.")
    print()
    print(f"{'SIGNAL':<18} {'N':>5} {'COV':>6} {'RHO':>7} {'YRS':>5} {'QD':>8}")
    for signal_name in GOALIE_WORKLOAD_SIGNALS:
        metric = next(item for item in result.metrics if item.signal_name == signal_name)
        coverage = (
            metric.player_seasons / result.baseline_player_seasons
            if result.baseline_player_seasons
            else 0.0
        )
        print(
            f"{SIGNAL_LABELS[signal_name]:<18} {metric.player_seasons:>5} "
            f"{coverage * 100:>5.1f}% {_fmt(metric.weighted_residual_rho):>7} "
            f"{metric.year_signs:>5} {_fmt_delta(metric.weighted_quartile_delta):>8}"
        )
    print()
    print("0 in YRS means |rho| < 0.02.")
    print("Diagnostic only. No goalie workload signal is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-workload-signal-summary":
        v44_cli.main(argv)
        return
    try:
        _draft_goalie_workload_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie workload signal screen error: {error}") from error

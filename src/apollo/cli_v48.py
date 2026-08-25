import argparse

from apollo import cli_v47 as v47_cli
from apollo.db import Database
from apollo.draft.goalie_rate_signal_backtest import GOALIE_RATE_SIGNALS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_rate_signal_backtest import run_goalie_rate_signal_aggregate

SIGNAL_LABELS = {
    "weighted_save_pct": "Weighted SV%",
    "latest_save_pct": "Latest SV%",
    "weighted_gaa": "Weighted GAA",
    "latest_gaa": "Latest GAA",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_delta(value: float | None, signal_name: str) -> str:
    if value is None:
        return "n/a"
    if "save_pct" in signal_name:
        return f"{value:+.4f}"
    return f"{value:+.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v47_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-rate-signal-summary",
        help="Screen source-only goalie SV% and GAA signals against baseline v0.1 residuals",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_rate_signal_summary(args: argparse.Namespace) -> None:
    result = run_goalie_rate_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    print("APOLLO GOALIE RATE RESIDUAL SIGNAL SCREEN")
    print()
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 goalie rates.")
    print(f"Baseline goalie player-seasons: {result.baseline_player_seasons}")
    print("SV% residual = actual target SV% - baseline projected SV%.")
    print("GAA residual = actual target GAA - baseline projected GAA.")
    print("Signals use source rates only; target rates are evaluation only.")
    print("Weighted = baseline 60/30/10 source rate. Latest = most recent source season.")
    print("RHO = signal rank correlation with its own target residual.")
    print("QD = top - bottom signal quartile residual.")
    print()
    print(f"{'SIGNAL':<14} {'N':>5} {'COV':>6} {'RHO':>7} {'YRS':>5} {'QD':>9}")
    for signal_name in GOALIE_RATE_SIGNALS:
        metric = next(item for item in result.metrics if item.signal_name == signal_name)
        coverage = metric.player_seasons / result.baseline_player_seasons
        print(
            f"{SIGNAL_LABELS[signal_name]:<14} {metric.player_seasons:>5} "
            f"{coverage * 100:>5.1f}% {_fmt_rho(metric.weighted_residual_rho):>7} "
            f"{metric.year_signs:>5} "
            f"{_fmt_delta(metric.weighted_quartile_delta, signal_name):>9}"
        )
    print()
    print("0 in YRS means |rho| < 0.02.")
    print("Diagnostic only. No goalie rate correction is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-rate-signal-summary":
        v47_cli.main(argv)
        return
    try:
        _draft_goalie_rate_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie rate signal screen error: {error}") from error

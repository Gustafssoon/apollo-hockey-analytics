import argparse

from apollo import cli_v48 as v48_cli
from apollo.db import Database
from apollo.draft.goalie_rate_candidate import GOALIE_RATE_VARIANTS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _fmt_value(stat_name: str, value: float) -> str:
    return f"{value:.4f}" if stat_name == "savePctg" else f"{value:.3f}"


def _fmt_gain(stat_name: str, value: float) -> str:
    return f"{value:+.4f}" if stat_name == "savePctg" else f"{value:+.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v48_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-rate-candidate-summary",
        help="Compare source-only goalie SV% and GAA mean-reversion candidates",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_rate_candidate_summary(args: argparse.Namespace) -> None:
    database = Database(args.db)
    baseline = run_goalie_baseline_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    result = run_goalie_rate_candidate_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    base_metrics = {metric.stat_name: metric for metric in baseline.metrics}

    print("APOLLO GOALIE RATE MEAN-REVERSION CANDIDATE SHOOTOUT")
    print()
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 goalie rates.")
    print("SV% and GAA are tested separately at 5%, 10%, and 20% mean reversion.")
    print("SV% priors use source-population saves / shots exposure.")
    print("GAA priors use source-population time-on-ice exposure.")
    print("Source-season priors are combined 60/30/10; target rates are evaluation only.")
    print("Each candidate changes only its own ratio stat. No SV% + GAA combinations.")
    print(f"Player-seasons: {result.baseline_player_seasons}")
    print()
    print(
        f"{'VARIANT':<8} {'STAT':<4} {'MAE':>8} {'GAIN':>9} {'STAT+':>6} "
        f"{'WORST':>9} {'RHO':>7} {'OTHER':>6}"
    )
    for spec in GOALIE_RATE_VARIANTS:
        variant = next(item for item in result.variants if item.spec.name == spec.name)
        metrics = {metric.stat_name: metric for metric in variant.metrics}
        target = metrics[spec.stat_name]
        base = base_metrics[spec.stat_name]
        exact = all(
            metrics[name].mae == base_metrics[name].mae
            and metrics[name].spearman_rho == base_metrics[name].spearman_rho
            for name in metrics
            if name != spec.stat_name
        )
        stat_label = "SV%" if spec.stat_name == "savePctg" else "GAA"
        print(
            f"{spec.name:<8} {stat_label:<4} {_fmt_value(spec.stat_name, target.mae):>8} "
            f"{_fmt_gain(spec.stat_name, base.mae - target.mae):>9} "
            f"{variant.improved_years:>1}/{len(result.target_seasons):<4} "
            f"{_fmt_gain(spec.stat_name, variant.worst_mae_gain):>9} "
            f"{_fmt_rho(target.spearman_rho):>7} {'EXACT' if exact else 'CHANGED':>6}"
        )
    print()
    print("Shootout only. No goalie rate candidate is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-rate-candidate-summary":
        v48_cli.main(argv)
        return
    try:
        _draft_goalie_rate_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie rate candidate error: {error}") from error

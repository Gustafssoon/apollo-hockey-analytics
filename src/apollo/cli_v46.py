import argparse

from apollo import cli_v45 as v45_cli
from apollo.db import Database
from apollo.draft.goalie_workload_context_candidate import (
    GOALIE_WORKLOAD_CONTEXT_VARIANTS,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_workload_context_candidate import (
    run_goalie_workload_context_candidate_aggregate,
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v45_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-workload-context-candidate-summary",
        help="Compare latest-role and age workload candidates against goalie baseline v0.1",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_workload_context_candidate_summary(args: argparse.Namespace) -> None:
    database = Database(args.db)
    baseline = run_goalie_baseline_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    result = run_goalie_workload_context_candidate_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    base_metrics = {metric.stat_name: metric for metric in baseline.metrics}

    print("APOLLO GOALIE WORKLOAD CONTEXT CANDIDATE SHOOTOUT")
    print()
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 historical GS.")
    print("Latest-share candidates use source-only latest-season goalie-share priors.")
    print("Age candidates use source-active goalie mean age and fixed per-year slopes.")
    print("Signals are tested separately. No latest-share + age combinations are included.")
    print("Only projected GS changes; W/SV/GA/SO follow unchanged baseline per-start rates.")
    print("SV% and GAA must remain exact. Target workload is evaluation only.")
    print(f"Player-seasons: {result.baseline_player_seasons}")
    print()
    print(
        f"{'VARIANT':<17} {'APPLIED':>9} {'GS MAE':>7} {'GS GAIN':>8} "
        f"{'GS+':>5} {'WORST':>8} {'GS RHO':>7} {'W GAIN':>7} "
        f"{'SV GAIN':>8} {'GA GAIN':>8} {'SO GAIN':>8} {'OTHER':>6}"
    )
    for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS:
        variant = next(item for item in result.variants if item.spec.name == spec.name)
        metrics = {metric.stat_name: metric for metric in variant.metrics}
        gs = metrics["gamesStarted"]
        exact = all(
            metrics[stat].mae == base_metrics[stat].mae
            and metrics[stat].spearman_rho == base_metrics[stat].spearman_rho
            for stat in ("savePctg", "goalsAgainstAvg")
        )
        print(
            f"{spec.name:<17} {variant.applied:>4}/{result.baseline_player_seasons:<4} "
            f"{gs.mae:>7.3f} {base_metrics['gamesStarted'].mae - gs.mae:>+8.3f} "
            f"{variant.improved_years:>1}/{len(result.target_seasons):<3} "
            f"{variant.worst_gs_mae_gain:>+8.3f} {_fmt_rho(gs.spearman_rho):>7} "
            f"{base_metrics['wins'].mae - metrics['wins'].mae:>+7.3f} "
            f"{base_metrics['saves'].mae - metrics['saves'].mae:>+8.3f} "
            f"{base_metrics['goalsAgainst'].mae - metrics['goalsAgainst'].mae:>+8.3f} "
            f"{base_metrics['shutouts'].mae - metrics['shutouts'].mae:>+8.3f} "
            f"{'EXACT' if exact else 'CHANGED':>6}"
        )
    print()
    print("Shootout only. No goalie workload context candidate is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if (
        args.command != "draft"
        or args.draft_command != "goalie-workload-context-candidate-summary"
    ):
        v45_cli.main(argv)
        return
    try:
        _draft_goalie_workload_context_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie workload context candidate error: {error}") from error

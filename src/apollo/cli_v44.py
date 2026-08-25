import argparse

from apollo import cli_v43 as v43_cli
from apollo.db import Database
from apollo.draft.goalie_workload_candidate import GOALIE_WORKLOAD_VARIANTS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_workload_candidate import run_goalie_workload_candidate_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v43_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-workload-candidate-summary",
        help=(
            "Compare schedule-normalized goalie workload candidates "
            "against baseline v0.1"
        ),
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_workload_candidate_summary(args: argparse.Namespace) -> None:
    database = Database(args.db)
    baseline = run_goalie_baseline_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    result = run_goalie_workload_candidate_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    print("APOLLO GOALIE WORKLOAD CANDIDATE SHOOTOUT")
    print()
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 historical GS.")
    print(
        "Candidates use source GS share normalized by scheduled team games, "
        "then project to 82."
    )
    print(
        "Only projected GS changes; W/SV/GA/SO follow unchanged baseline "
        "per-start rates."
    )
    print("SV% and GAA must remain exact. Target workload is evaluation only.")
    print(f"Player-seasons: {result.baseline_player_seasons}")
    print()
    print(
        f"{'VARIANT':<14} {'GS MAE':>7} {'GS GAIN':>8} {'GS+':>5} {'WORST':>8} "
        f"{'GS RHO':>7} {'W GAIN':>7} {'SV GAIN':>8} {'GA GAIN':>8} "
        f"{'SO GAIN':>8} {'OTHER':>6}"
    )
    base_metrics = {metric.stat_name: metric for metric in baseline.metrics}
    for name, _ in GOALIE_WORKLOAD_VARIANTS:
        variant = next(item for item in result.variants if item.name == name)
        metrics = {metric.stat_name: metric for metric in variant.metrics}
        gs = metrics["gamesStarted"]
        exact = all(
            metrics[stat].mae == base_metrics[stat].mae
            and metrics[stat].spearman_rho == base_metrics[stat].spearman_rho
            for stat in ("savePctg", "goalsAgainstAvg")
        )
        print(
            f"{name:<14} {gs.mae:>7.3f} "
            f"{base_metrics['gamesStarted'].mae - gs.mae:>+8.3f} "
            f"{variant.improved_years:>1}/{len(result.target_seasons):<3} "
            f"{variant.worst_gs_mae_gain:>+8.3f} {_fmt_rho(gs.spearman_rho):>7} "
            f"{base_metrics['wins'].mae - metrics['wins'].mae:>+7.3f} "
            f"{base_metrics['saves'].mae - metrics['saves'].mae:>+8.3f} "
            f"{base_metrics['goalsAgainst'].mae - metrics['goalsAgainst'].mae:>+8.3f} "
            f"{base_metrics['shutouts'].mae - metrics['shutouts'].mae:>+8.3f} "
            f"{'EXACT' if exact else 'CHANGED':>6}"
        )
    print()
    print("Shootout only. No goalie workload candidate is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if (
        args.command != "draft"
        or args.draft_command != "goalie-workload-candidate-summary"
    ):
        v43_cli.main(argv)
        return
    try:
        _draft_goalie_workload_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie workload candidate error: {error}") from error

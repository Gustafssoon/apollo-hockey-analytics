import argparse

from apollo import cli_v46 as v46_cli
from apollo.db import Database
from apollo.draft.goalie_age_candidate_gate import GOALIE_AGE_GATE_COHORTS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_age_candidate_gate import run_goalie_age_candidate_gate
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_workload_context_candidate import (
    run_goalie_workload_context_candidate_aggregate,
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _metrics_by_name(metrics):
    return {metric.stat_name: metric for metric in metrics}


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v46_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-age-candidate-gate",
        help="Run the locked 0.5% goalie-age workload robustness gate",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    return parser


def _approved_equivalence(database: Database, season: int, years: int, gate) -> bool:
    primary = next(item for item in gate.cohorts if item.cohort.name == "GS20 ALL")
    shootout = run_goalie_workload_context_candidate_aggregate(
        database,
        season,
        years=years,
        min_actual_starts=20,
    )
    approved = next(item for item in shootout.variants if item.spec.name == "age-0.5")
    baseline = run_goalie_baseline_aggregate(
        database,
        season,
        years=years,
        min_actual_starts=20,
    )
    if primary.player_seasons != approved.player_seasons:
        return False
    if primary.applied != approved.applied:
        return False
    approved_metrics = _metrics_by_name(approved.metrics)
    candidate_metrics = _metrics_by_name(primary.candidate_metrics)
    baseline_metrics = _metrics_by_name(primary.baseline_metrics)
    production_baseline_metrics = _metrics_by_name(baseline.metrics)
    return all(
        candidate_metrics[name].mae == approved_metrics[name].mae
        and candidate_metrics[name].spearman_rho == approved_metrics[name].spearman_rho
        and baseline_metrics[name].mae == production_baseline_metrics[name].mae
        and baseline_metrics[name].spearman_rho
        == production_baseline_metrics[name].spearman_rho
        for name in candidate_metrics
    )


def _draft_goalie_age_candidate_gate(args: argparse.Namespace) -> None:
    database = Database(args.db)
    result = run_goalie_age_candidate_gate(database, args.season, years=args.years)
    print("APOLLO GOALIE AGE 0.5% WORKLOAD CANDIDATE ROBUSTNESS GATE")
    print()
    print("Candidate locked: 0.5% projected-GS adjustment per year from source-active mean age.")
    print("Baseline: apollo-goalie-baseline-v0.1 raw 60/30/10 historical GS.")
    print("Age prior is source-only and is not rebuilt inside age subgroups.")
    print("Only GS changes; W/SV/GA/SO follow unchanged baseline per-start rates.")
    print("SV% and GAA must remain exact. Target workload is evaluation only.")
    print()
    print(
        f"{'COHORT':<14} {'N':>4} {'APPLIED':>9} {'GS GAIN':>8} {'GS+':>5} "
        f"{'WORST':>8} {'GS RHO':>7} {'W GAIN':>7} {'SV GAIN':>8} "
        f"{'GA GAIN':>8} {'SO GAIN':>8} {'OTHER':>6}"
    )
    for cohort_spec in GOALIE_AGE_GATE_COHORTS:
        cohort = next(item for item in result.cohorts if item.cohort == cohort_spec)
        base = _metrics_by_name(cohort.baseline_metrics)
        candidate = _metrics_by_name(cohort.candidate_metrics)
        exact = all(
            base[name].mae == candidate[name].mae
            and base[name].spearman_rho == candidate[name].spearman_rho
            for name in ("savePctg", "goalsAgainstAvg")
        )
        gs = candidate["gamesStarted"]
        print(
            f"{cohort_spec.name:<14} {cohort.player_seasons:>4} "
            f"{cohort.applied:>4}/{cohort.player_seasons:<4} "
            f"{base['gamesStarted'].mae - gs.mae:>+8.3f} "
            f"{cohort.improved_years:>1}/{len(result.target_seasons):<3} "
            f"{cohort.worst_gs_mae_gain:>+8.3f} {_fmt_rho(gs.spearman_rho):>7} "
            f"{base['wins'].mae - candidate['wins'].mae:>+7.3f} "
            f"{base['saves'].mae - candidate['saves'].mae:>+8.3f} "
            f"{base['goalsAgainst'].mae - candidate['goalsAgainst'].mae:>+8.3f} "
            f"{base['shutouts'].mae - candidate['shutouts'].mae:>+8.3f} "
            f"{'EXACT' if exact else 'CHANGED':>6}"
        )
    print()
    equivalent = _approved_equivalence(database, args.season, args.years, result)
    print(f"Approved-candidate equivalence: {'EXACT' if equivalent else 'MISMATCH'}")
    print("Candidate gate only. No goalie workload model is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-age-candidate-gate":
        v46_cli.main(argv)
        return
    try:
        _draft_goalie_age_candidate_gate(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie age candidate gate error: {error}") from error

import argparse

from apollo import cli_v49 as v49_cli
from apollo.db import Database
from apollo.draft.goalie_rate_candidate_gate import (
    GOALIE_RATE_GATE_CANDIDATES,
    GOALIE_RATE_GATE_COHORTS,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_aggregate
from apollo.services.goalie_rate_candidate_gate import run_goalie_rate_candidate_gate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _metrics_by_name(metrics):
    return {metric.stat_name: metric for metric in metrics}


def _fmt_value(stat_name: str, value: float) -> str:
    return f"{value:+.4f}" if stat_name == "savePctg" else f"{value:+.3f}"


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v49_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-rate-candidate-gate",
        help="Run locked 5% SV% and GAA goalie-rate robustness gates",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    return parser


def _candidate_equivalence(
    database: Database,
    season: int,
    years: int,
    gate,
    candidate_name: str,
) -> bool:
    primary = next(
        row
        for row in gate.rows
        if row.cohort.name == "GS20 ALL" and row.candidate_name == candidate_name
    )
    shootout = run_goalie_rate_candidate_aggregate(database, season, years=years)
    approved = next(item for item in shootout.variants if item.spec.name == candidate_name)
    baseline = run_goalie_baseline_aggregate(database, season, years=years)
    if primary.player_seasons != approved.player_seasons:
        return False

    baseline_metrics = _metrics_by_name(primary.baseline_metrics)
    gate_metrics = _metrics_by_name(primary.candidate_metrics)
    approved_metrics = _metrics_by_name(approved.metrics)
    production_baseline_metrics = _metrics_by_name(baseline.metrics)
    return all(
        gate_metrics[name].mae == approved_metrics[name].mae
        and gate_metrics[name].spearman_rho == approved_metrics[name].spearman_rho
        and baseline_metrics[name].mae == production_baseline_metrics[name].mae
        and baseline_metrics[name].spearman_rho
        == production_baseline_metrics[name].spearman_rho
        for name in gate_metrics
    )


def _draft_goalie_rate_candidate_gate(args: argparse.Namespace) -> None:
    database = Database(args.db)
    result = run_goalie_rate_candidate_gate(database, args.season, years=args.years)

    print("APOLLO GOALIE RATE 5% CANDIDATE ROBUSTNESS GATES")
    print()
    print("Candidates locked: sv-5 and gaa-5 from the approved single-category shootout.")
    print("Source priors are global and are not rebuilt inside GS or age subgroups.")
    print("Each candidate changes only its own ratio stat; all other goalie stats stay exact.")
    print("Target rates are evaluation only.")
    print()
    print(
        f"{'CAND':<6} {'COHORT':<14} {'N':>4} {'GAIN':>9} {'STAT+':>6} "
        f"{'WORST':>9} {'RHO':>7} {'OTHER':>6}"
    )
    for candidate_name in GOALIE_RATE_GATE_CANDIDATES:
        for cohort_spec in GOALIE_RATE_GATE_COHORTS:
            row = next(
                item
                for item in result.rows
                if item.candidate_name == candidate_name and item.cohort == cohort_spec
            )
            base = _metrics_by_name(row.baseline_metrics)
            candidate = _metrics_by_name(row.candidate_metrics)
            target = candidate[row.stat_name]
            exact = all(
                candidate[name].mae == base[name].mae
                and candidate[name].spearman_rho == base[name].spearman_rho
                for name in candidate
                if name != row.stat_name
            )
            print(
                f"{candidate_name:<6} {cohort_spec.name:<14} {row.player_seasons:>4} "
                f"{_fmt_value(row.stat_name, base[row.stat_name].mae - target.mae):>9} "
                f"{row.improved_years:>1}/{len(result.target_seasons):<4} "
                f"{_fmt_value(row.stat_name, row.worst_mae_gain):>9} "
                f"{_fmt_rho(target.spearman_rho):>7} {'EXACT' if exact else 'CHANGED':>6}"
            )
    print()
    for candidate_name in GOALIE_RATE_GATE_CANDIDATES:
        equivalent = _candidate_equivalence(
            database,
            args.season,
            args.years,
            result,
            candidate_name,
        )
        print(
            f"{candidate_name} approved-candidate equivalence: "
            f"{'EXACT' if equivalent else 'MISMATCH'}"
        )
    print("Candidate gates only. No goalie rate model is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-rate-candidate-gate":
        v49_cli.main(argv)
        return
    try:
        _draft_goalie_rate_candidate_gate(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie rate candidate gate error: {error}") from error

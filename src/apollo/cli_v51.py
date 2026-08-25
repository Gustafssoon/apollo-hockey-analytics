import argparse

from apollo import cli_v50 as v50_cli
from apollo.db import Database
from apollo.draft.goalie_baseline_v02_candidate import (
    GOALIE_BASELINE_V02_CANDIDATE_VERSION,
)
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_baseline import run_goalie_baseline_aggregate
from apollo.services.goalie_baseline_v02_candidate import (
    run_goalie_baseline_v02_candidate_aggregate,
)
from apollo.services.goalie_rate_candidate import run_goalie_rate_candidate_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _metrics_by_name(metrics):
    return {metric.stat_name: metric for metric in metrics}


def _fmt(stat_name: str, value: float) -> str:
    if stat_name == "savePctg":
        return f"{value:.4f}"
    return f"{value:.3f}"


def _fmt_gain(stat_name: str, value: float) -> str:
    if stat_name == "savePctg":
        return f"{value:+.4f}"
    return f"{value:+.3f}"


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v50_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-baseline-v02-candidate-summary",
        help="Integrate approved 5% SV% and GAA rate regressions into goalie baseline v0.2 candidate",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _component_equivalence(
    database: Database,
    season: int,
    years: int,
    min_actual_starts: int,
    candidate,
) -> tuple[bool, bool, bool]:
    rate = run_goalie_rate_candidate_aggregate(
        database,
        season,
        years=years,
        min_actual_starts=min_actual_starts,
    )
    baseline = run_goalie_baseline_aggregate(
        database,
        season,
        years=years,
        min_actual_starts=min_actual_starts,
    )
    candidate_metrics = _metrics_by_name(candidate.candidate_metrics)
    baseline_metrics = _metrics_by_name(candidate.baseline_metrics)
    production_baseline = _metrics_by_name(baseline.metrics)
    sv = next(item for item in rate.variants if item.spec.name == "sv-5")
    gaa = next(item for item in rate.variants if item.spec.name == "gaa-5")
    sv_metrics = _metrics_by_name(sv.metrics)
    gaa_metrics = _metrics_by_name(gaa.metrics)

    sv_exact = (
        candidate_metrics["savePctg"].mae == sv_metrics["savePctg"].mae
        and candidate_metrics["savePctg"].spearman_rho
        == sv_metrics["savePctg"].spearman_rho
    )
    gaa_exact = (
        candidate_metrics["goalsAgainstAvg"].mae == gaa_metrics["goalsAgainstAvg"].mae
        and candidate_metrics["goalsAgainstAvg"].spearman_rho
        == gaa_metrics["goalsAgainstAvg"].spearman_rho
    )
    other_exact = all(
        candidate_metrics[name].mae == production_baseline[name].mae
        and candidate_metrics[name].spearman_rho == production_baseline[name].spearman_rho
        and baseline_metrics[name].mae == production_baseline[name].mae
        and baseline_metrics[name].spearman_rho == production_baseline[name].spearman_rho
        for name in ("gamesStarted", "wins", "saves", "goalsAgainst", "shutouts")
    )
    return sv_exact, gaa_exact, other_exact


def _draft_goalie_baseline_v02_candidate_summary(args: argparse.Namespace) -> None:
    database = Database(args.db)
    result = run_goalie_baseline_v02_candidate_aggregate(
        database,
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )
    baseline = _metrics_by_name(result.baseline_metrics)
    candidate = _metrics_by_name(result.candidate_metrics)

    print("APOLLO GOALIE BASELINE v0.2 RATE INTEGRATION CANDIDATE")
    print()
    print(f"Candidate model: {GOALIE_BASELINE_V02_CANDIDATE_VERSION}")
    print("Base: goalie baseline v0.1 workload and total-rate projections.")
    print("Integrated components: sv-5 and gaa-5 only.")
    print("No goalie age workload adjustment, team context, or other tuning is included.")
    print(f"Player-seasons: {result.player_seasons}")
    print()
    print(f"{'STAT':<8} {'BASE MAE':>10} {'CAND MAE':>10} {'GAIN':>10} {'BASE RHO':>9} {'CAND RHO':>9}")
    for stat_name in baseline:
        base = baseline[stat_name]
        cand = candidate[stat_name]
        label = {
            "gamesStarted": "GS",
            "wins": "W",
            "saves": "SV",
            "goalsAgainst": "GA",
            "shutouts": "SO",
            "savePctg": "SV%",
            "goalsAgainstAvg": "GAA",
        }[stat_name]
        print(
            f"{label:<8} {_fmt(stat_name, base.mae):>10} {_fmt(stat_name, cand.mae):>10} "
            f"{_fmt_gain(stat_name, base.mae - cand.mae):>10} "
            f"{_fmt_rho(base.spearman_rho):>9} {_fmt_rho(cand.spearman_rho):>9}"
        )

    sv_exact, gaa_exact, other_exact = _component_equivalence(
        database,
        args.season,
        args.years,
        args.min_actual_starts,
        result,
    )
    print()
    print(f"sv-5 component equivalence: {'EXACT' if sv_exact else 'MISMATCH'}")
    print(f"gaa-5 component equivalence: {'EXACT' if gaa_exact else 'MISMATCH'}")
    print(f"v0.1 non-rate equivalence: {'EXACT' if other_exact else 'MISMATCH'}")
    print("Integration candidate only. No goalie production model is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-baseline-v02-candidate-summary":
        v50_cli.main(argv)
        return
    try:
        _draft_goalie_baseline_v02_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie baseline v0.2 candidate error: {error}") from error

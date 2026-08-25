import argparse

from apollo import cli_v33 as v33_cli
from apollo.db import Database
from apollo.draft.overall_finishing_candidate_gate import (
    OVERALL_SHOOTING_CANDIDATE_VERSION,
)
from apollo.draft.projections import ProjectionError
from apollo.services.overall_finishing_candidate_gate import (
    run_overall_finishing_candidate_gate,
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(cohort, stat_name: str):
    return next(metric for metric in cohort.metrics if metric.stat_name == stat_name)


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _untouched_exact(cohort) -> bool:
    for stat_name in (
        "gamesPlayed",
        "assists",
        "powerPlayPoints",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _metric(cohort, stat_name)
        if metric.baseline_mae != metric.candidate_mae:
            return False
        if metric.baseline_rho != metric.candidate_rho:
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = v33_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    gate_parser = draft_subparsers.add_parser(
        "overall-finishing-candidate-gate",
        help="Robustness gate fixed Overall SH% 5% candidate against production v0.6",
    )
    gate_parser.add_argument("--season", type=int, required=True)
    gate_parser.add_argument("--years", type=int, default=3)
    gate_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    gate_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_overall_finishing_candidate_gate(args: argparse.Namespace) -> None:
    result = run_overall_finishing_candidate_gate(
        Database(args.db),
        args.season,
        years=args.years,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO OVERALL SH% 5% CANDIDATE ROBUSTNESS GATE")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Candidate: {OVERALL_SHOOTING_CANDIDATE_VERSION}")
    print("Strength is locked at 5%; this gate does not tune the parameter.")
    print("Production baseline is v0.6. Candidate changes G only.")
    print("Priors/signals are source-only; target stats are evaluation only.")
    print()
    print(
        f"{'COHORT':<10} {'N':>5} {'APPLIED':>9} "
        f"{'G GAIN':>7} {'G+':>4} {'G WORST':>8} {'G RHO':>7} "
        f"{'P GAIN':>7} {'P+':>4} {'P WORST':>8} {'P RHO':>7} "
        f"{'TOP25':>7} {'OTHER':>6}"
    )
    for cohort in result.cohorts:
        goals = _metric(cohort, "goals")
        points = _metric(cohort, "points")
        print(
            f"{cohort.label:<10} {cohort.player_seasons:>5} "
            f"{cohort.applied:>4}/{cohort.player_seasons:<4} "
            f"{goals.mae_gain:>+7.3f} "
            f"{cohort.goals_improved_years:>1}/{len(result.target_seasons):<2} "
            f"{cohort.worst_goals_gain:>+8.3f} {_fmt_rho(goals.candidate_rho):>7} "
            f"{points.mae_gain:>+7.3f} "
            f"{cohort.points_improved_years:>1}/{len(result.target_seasons):<2} "
            f"{cohort.worst_points_gain:>+8.3f} {_fmt_rho(points.candidate_rho):>7} "
            f"{cohort.candidate_top25 * 100:>6.1f}% "
            f"{'EXACT' if _untouched_exact(cohort) else 'CHANGED':>6}"
        )

    print()
    print("Cohorts are predeclared: GP20/30/40 ALL plus GP20 F and GP20 D.")
    print("OTHER checks GP/A/PPP/SOG/HIT/BLK against exact production v0.6.")
    print("Candidate gate only. Production remains apollo-skater-baseline-v0.6.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "overall-finishing-candidate-gate":
        v33_cli.main(argv)
        return

    try:
        _draft_overall_finishing_candidate_gate(args)
    except ProjectionError as error:
        raise SystemExit(f"Overall finishing candidate gate error: {error}") from error

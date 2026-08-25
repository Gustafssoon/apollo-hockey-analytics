import argparse

from apollo import cli_v34 as v34_cli
from apollo.db import Database
from apollo.draft.overall_finishing import OVERALL_FINISHING_MODEL_VERSION
from apollo.draft.projections import MODEL_VERSION, ProjectionError
from apollo.services.overall_finishing_production_gate import (
    run_overall_finishing_production_gate,
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
    parser = v34_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    gate_parser = draft_subparsers.add_parser(
        "overall-finishing-production-gate",
        help="Verify production v0.7 exactly reproduces the approved Overall SH% 5% candidate",
    )
    gate_parser.add_argument("--season", type=int, required=True)
    gate_parser.add_argument("--years", type=int, default=3)
    gate_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    gate_parser.add_argument("--min-actual-games", type=int, default=20)
    gate_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_overall_finishing_production_gate(args: argparse.Namespace) -> None:
    result = run_overall_finishing_production_gate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    cohort = result.aggregate
    goals = _metric(cohort, "goals")
    points = _metric(cohort, "points")

    print("APOLLO OVERALL SH% 5% PRODUCTION v0.7 GATE")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production model: {MODEL_VERSION}")
    print(f"Finishing layer: {OVERALL_FINISHING_MODEL_VERSION}")
    print("Approved candidate: global Overall SH% 5%, source-only F/D priors, 3/3 context.")
    print("Missing context uses exact pre-finishing numerical fallback.")
    print()
    print(f"{'SEASON':<9} {'N':>5} {'APPLIED':>9} {'EQUIV':>7}")
    for check in result.season_checks:
        print(
            f"{_season_label(check.target_season):<9} {check.evaluated_players:>5} "
            f"{check.applied:>4}/{check.evaluated_players:<4} "
            f"{'EXACT' if check.exact_candidate_equivalence else 'FAIL':>7}"
        )

    print()
    print(
        f"{'N':>5} {'G MAE':>7} {'G GAIN':>7} {'G+':>4} {'G WORST':>8} {'G RHO':>7} "
        f"{'PTS MAE':>8} {'P GAIN':>7} {'P+':>4} {'P WORST':>8} {'P RHO':>7} "
        f"{'TOP25':>7} {'OTHER':>6}"
    )
    print(
        f"{cohort.player_seasons:>5} {goals.candidate_mae:>7.3f} {goals.mae_gain:>+7.3f} "
        f"{cohort.goals_improved_years:>1}/{len(result.target_seasons):<2} "
        f"{cohort.worst_goals_gain:>+8.3f} {_fmt_rho(goals.candidate_rho):>7} "
        f"{points.candidate_mae:>8.3f} {points.mae_gain:>+7.3f} "
        f"{cohort.points_improved_years:>1}/{len(result.target_seasons):<2} "
        f"{cohort.worst_points_gain:>+8.3f} {_fmt_rho(points.candidate_rho):>7} "
        f"{cohort.candidate_top25 * 100:>6.1f}% "
        f"{'EXACT' if _untouched_exact(cohort) else 'CHANGED':>6}"
    )
    print()
    print(
        "Approved-candidate equivalence: "
        + ("EXACT" if result.exact_candidate_equivalence else "FAILED")
    )
    print("Production is not merged until Ruff, full pytest, and this gate are clean.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "overall-finishing-production-gate":
        v34_cli.main(argv)
        return

    try:
        _draft_overall_finishing_production_gate(args)
    except ProjectionError as error:
        raise SystemExit(f"Overall finishing production gate error: {error}") from error

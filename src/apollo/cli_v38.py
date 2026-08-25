import argparse

from apollo import cli_v37 as v37_cli
from apollo.db import Database
from apollo.draft.pp_deployment_candidate_gate import (
    PP_DEPLOYMENT_CANDIDATE_SIGNAL,
    PP_DEPLOYMENT_CANDIDATE_STRENGTH,
    PP_DEPLOYMENT_CANDIDATE_VERSION,
)
from apollo.draft.projections import ProjectionError
from apollo.services.pp_deployment_candidate_gate import run_pp_deployment_candidate_gate


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


def _other_exact(cohort) -> bool:
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
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
    parser = v37_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    gate_parser = draft_subparsers.add_parser(
        "pp-deployment-candidate-gate",
        help="Robustness gate fixed PP TOI/GP 5% PPP candidate against production v0.7",
    )
    gate_parser.add_argument("--season", type=int, required=True)
    gate_parser.add_argument("--years", type=int, default=3)
    gate_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    gate_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_pp_deployment_candidate_gate(args: argparse.Namespace) -> None:
    result = run_pp_deployment_candidate_gate(
        Database(args.db),
        args.season,
        years=args.years,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO PP TOI/GP 5% PPP CANDIDATE ROBUSTNESS GATE")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Candidate: {PP_DEPLOYMENT_CANDIDATE_VERSION}")
    print(
        f"Signal/strength locked: {PP_DEPLOYMENT_CANDIDATE_SIGNAL} "
        f"at {PP_DEPLOYMENT_CANDIDATE_STRENGTH * 100:.0f}%."
    )
    print("Production baseline is v0.7. Candidate changes PPP only.")
    print("Priors/signals are source-only; target deployment is never used as a feature.")
    print()
    print(
        f"{'COHORT':<10} {'N':>5} {'APPLIED':>9} {'PPP GAIN':>8} "
        f"{'PPP+':>5} {'WORST':>8} {'PPP RHO':>7} {'OTHER':>6}"
    )
    for cohort in result.cohorts:
        ppp = _metric(cohort, "powerPlayPoints")
        print(
            f"{cohort.label:<10} {cohort.player_seasons:>5} "
            f"{cohort.applied:>4}/{cohort.player_seasons:<4} "
            f"{ppp.mae_gain:>+8.3f} "
            f"{cohort.ppp_improved_years:>1}/{len(result.target_seasons):<3} "
            f"{cohort.worst_ppp_gain:>+8.3f} {_fmt_rho(ppp.candidate_rho):>7} "
            f"{'EXACT' if _other_exact(cohort) else 'CHANGED':>6}"
        )

    print()
    print("Cohorts are predeclared: GP20/30/40 ALL plus GP20 F and GP20 D.")
    print("OTHER checks GP/PTS/G/A/SOG/HIT/BLK against exact production v0.7.")
    print("Candidate gate only. Production remains apollo-skater-baseline-v0.7.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "pp-deployment-candidate-gate":
        v37_cli.main(argv)
        return

    try:
        _draft_pp_deployment_candidate_gate(args)
    except ProjectionError as error:
        raise SystemExit(f"PP deployment candidate gate error: {error}") from error

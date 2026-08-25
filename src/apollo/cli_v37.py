import argparse

from apollo import cli_v36 as v36_cli
from apollo.db import Database
from apollo.draft.pp_deployment_candidate import (
    PP_DEPLOYMENT_SIGNALS,
    PP_DEPLOYMENT_STRENGTHS,
)
from apollo.draft.projections import MODEL_VERSION, ProjectionError
from apollo.services.pp_deployment_candidate import run_pp_deployment_candidate_aggregate

SIGNAL_LABELS = {
    "pp_toi_ratio": "PP TOI/GP",
    "pp_toi_share_ratio": "PP TOI share",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(variant, stat_name: str):
    return next(metric for metric in variant.metrics if metric.stat_name == stat_name)


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _untouched_exact(variant) -> bool:
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _metric(variant, stat_name)
        if metric.baseline_mae != metric.candidate_mae:
            return False
        if metric.baseline_rho != metric.candidate_rho:
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = v36_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "pp-deployment-ppp-candidate-summary",
        help="Shoot out source-only PP deployment mean reversion for PPP against production v0.7",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_pp_deployment_ppp_candidate_summary(args: argparse.Namespace) -> None:
    result = run_pp_deployment_candidate_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO PP DEPLOYMENT / PPP CANDIDATE SHOOTOUT")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production baseline: {MODEL_VERSION}")
    print(f"Production v0.7 player-seasons: {result.baseline_player_seasons}")
    print("Candidates: 5%, 10%, 20% source-only F/D-normalized PP deployment mean reversion.")
    print("Only projected PPP changes; missing 3/3 signal context uses exact production v0.7 fallback.")
    print("Target deployment is never used as a feature; target PPP is evaluation only.")
    print()
    print(
        f"{'SIGNAL':<13} {'STR':>4} {'APPLIED':>9} "
        f"{'PPP MAE':>8} {'GAIN':>7} {'PPP+':>5} {'WORST':>8} {'PPP RHO':>8} {'OTHER':>6}"
    )
    for signal_name in PP_DEPLOYMENT_SIGNALS:
        for strength in PP_DEPLOYMENT_STRENGTHS:
            variant = next(
                item
                for item in result.variants
                if item.signal_name == signal_name and item.strength == strength
            )
            ppp = _metric(variant, "powerPlayPoints")
            print(
                f"{SIGNAL_LABELS[signal_name]:<13} {int(strength * 100):>3}% "
                f"{variant.applied:>4}/{result.baseline_player_seasons:<4} "
                f"{ppp.candidate_mae:>8.3f} {ppp.mae_gain:>+7.3f} "
                f"{variant.ppp_improved_years:>1}/{len(result.target_seasons):<3} "
                f"{variant.worst_ppp_mae_gain:>+8.3f} {_fmt_rho(ppp.candidate_rho):>8} "
                f"{'EXACT' if _untouched_exact(variant) else 'CHANGED':>6}"
            )

    print()
    print("OTHER checks GP/PTS/G/A/SOG/HIT/BLK MAE and rho against exact production v0.7.")
    print("Shootout only. No PP deployment candidate is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "pp-deployment-ppp-candidate-summary":
        v36_cli.main(argv)
        return

    try:
        _draft_pp_deployment_ppp_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"PP deployment PPP candidate summary error: {error}") from error

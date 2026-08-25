import argparse

from apollo import cli_v32 as v32_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.draft.shot_type_finishing_candidate import (
    SHOT_TYPE_FINISHING_SIGNALS,
    SHOT_TYPE_FINISHING_STRENGTHS,
)
from apollo.services.shot_type_finishing_candidate import (
    run_shot_type_finishing_candidate_aggregate,
)

SIGNAL_LABELS = {
    "overall_shooting_pct": "Overall SH%",
    "tip_deflect_shooting_pct": "Tip+Defl SH%",
    "wrist_shooting_pct": "Wrist SH%",
    "snap_shooting_pct": "Snap SH%",
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
        "assists",
        "powerPlayPoints",
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
    parser = v32_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "shot-type-finishing-candidate-summary",
        help="Shoot out source-only shot-type finishing mean reversion against production v0.6",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_shot_type_finishing_candidate_summary(args: argparse.Namespace) -> None:
    result = run_shot_type_finishing_candidate_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO SHOT-TYPE FINISHING CANDIDATE SHOOTOUT")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production v0.6 player-seasons: {result.baseline_player_seasons}")
    print("Candidates: 5%, 10%, 20% source-only F/D finishing mean reversion.")
    print("Only projected G changes; missing 3/3 context uses exact production v0.6 fallback.")
    print("Priors and player signals use source seasons only; target stats are evaluation only.")
    print()
    print(
        f"{'SIGNAL':<14} {'STR':>4} {'APPLIED':>9} "
        f"{'G MAE':>7} {'G GAIN':>7} {'G+':>4} {'G WORST':>8} {'G RHO':>7} "
        f"{'PTS MAE':>8} {'P GAIN':>7} {'P+':>4} {'P WORST':>8} {'P RHO':>7} "
        f"{'TOP25':>7} {'OTHER':>6}"
    )
    for signal_name in SHOT_TYPE_FINISHING_SIGNALS:
        for strength in SHOT_TYPE_FINISHING_STRENGTHS:
            variant = next(
                item
                for item in result.variants
                if item.signal_name == signal_name and item.strength == strength
            )
            goals = _metric(variant, "goals")
            points = _metric(variant, "points")
            print(
                f"{SIGNAL_LABELS[signal_name]:<14} {int(strength * 100):>3}% "
                f"{variant.applied:>4}/{result.baseline_player_seasons:<4} "
                f"{goals.candidate_mae:>7.3f} {goals.mae_gain:>+7.3f} "
                f"{variant.goals_improved_years:>1}/{len(result.target_seasons):<2} "
                f"{variant.worst_goals_mae_gain:>+8.3f} {_fmt_rho(goals.candidate_rho):>7} "
                f"{points.candidate_mae:>8.3f} {points.mae_gain:>+7.3f} "
                f"{variant.points_improved_years:>1}/{len(result.target_seasons):<2} "
                f"{variant.worst_points_mae_gain:>+8.3f} {_fmt_rho(points.candidate_rho):>7} "
                f"{variant.candidate_top25_overlap_rate * 100:>6.1f}% "
                f"{'EXACT' if _untouched_exact(variant) else 'CHANGED':>6}"
            )

    print()
    print("OTHER checks GP/A/PPP/SOG/HIT/BLK MAE and rho against exact production v0.6.")
    print("Shootout only. No shot-type candidate is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "shot-type-finishing-candidate-summary":
        v32_cli.main(argv)
        return

    try:
        _draft_shot_type_finishing_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Shot-type finishing candidate summary error: {error}") from error

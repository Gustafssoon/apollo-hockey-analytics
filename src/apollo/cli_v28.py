import argparse

from apollo import cli_v27 as v27_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.draft.v05_candidate import (
    V05_CANDIDATE_MODEL_VERSION,
    V05_CANDIDATE_SHOOTING_STRENGTH,
)
from apollo.services.v05_candidate import run_v05_candidate_aggregate

STAT_LABELS = {
    "points": "PTS",
    "goals": "G",
    "assists": "A",
    "shots": "SOG",
    "powerPlayPoints": "PPP",
    "hits": "HIT",
    "blockedShots": "BLK",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(result, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _top25(result) -> float:
    return next(
        overlap.overlap_rate
        for overlap in result.top_k_points
        if overlap.requested_k == 25
    )


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v27_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "v05-candidate-summary",
        help="Run the sh_offense_10 v0.5 candidate against the full v0.4 backtest sample",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_v05_candidate_summary(args: argparse.Namespace) -> None:
    result = run_v05_candidate_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO SKATER v0.5 CANDIDATE FULL-SAMPLE GATE")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Candidate: {V05_CANDIDATE_MODEL_VERSION}")
    print(
        f"5v5 SH% offense correction: {V05_CANDIDATE_SHOOTING_STRENGTH * 100:.0f}% "
        "toward source-season F/D context"
    )
    print(
        f"Shooting context applied: {result.shooting_context_applied}/"
        f"{result.baseline_player_seasons} player-seasons; all others use exact v0.4 fallback."
    )
    print()
    print(
        f"{'SEASON':<9} {'N':>4} {'SH':>4} {'B PTS':>7} {'C PTS':>7} {'GAIN':>7} "
        f"{'B RHO':>6} {'C RHO':>6} {'B T25':>6} {'C T25':>6}"
    )
    for season_result in result.season_results:
        baseline_pts = _metric(season_result.baseline, "points")
        candidate_pts = _metric(season_result.candidate, "points")
        print(
            f"{_season_label(season_result.target_season):<9} "
            f"{season_result.baseline.evaluated_players:>4} "
            f"{season_result.shooting_context_applied:>4} "
            f"{baseline_pts.mae:>7.3f} {candidate_pts.mae:>7.3f} "
            f"{baseline_pts.mae - candidate_pts.mae:>+7.3f} "
            f"{_fmt_rho(baseline_pts.spearman_rho):>6} "
            f"{_fmt_rho(candidate_pts.spearman_rho):>6} "
            f"{_top25(season_result.baseline) * 100:>5.1f}% "
            f"{_top25(season_result.candidate) * 100:>5.1f}%"
        )

    print()
    print("Pooled metrics")
    print("--------------")
    print(f"{'STAT':<5} {'BASE MAE':>9} {'CAND MAE':>9} {'GAIN':>8} {'BASE RHO':>9} {'CAND RHO':>9}")
    for metric in result.metrics:
        print(
            f"{STAT_LABELS[metric.stat_name]:<5} {metric.baseline_mae:>9.3f} "
            f"{metric.candidate_mae:>9.3f} {metric.mae_gain:>+8.3f} "
            f"{_fmt_rho(metric.baseline_rho):>9} {_fmt_rho(metric.candidate_rho):>9}"
        )

    print()
    print(
        f"PTS improved years: {result.points_improved_years}/{len(result.target_seasons)} | "
        f"worst-year gain {result.worst_points_mae_gain:+.3f}"
    )
    print(
        f"Average Top-25: v0.4 {result.baseline_top25_overlap_rate * 100:.1f}% | "
        f"candidate {result.candidate_top25_overlap_rate * 100:.1f}%"
    )
    print("Candidate gate only. Production remains apollo-skater-baseline-v0.4.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "v05-candidate-summary":
        v27_cli.main(argv)
        return

    try:
        _draft_v05_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"v0.5 candidate summary error: {error}") from error
